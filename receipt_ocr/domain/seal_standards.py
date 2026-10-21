"""Per-stamp comparison against a manually registered standard seal."""
from copy import deepcopy
import hashlib

from .decision import decide_overall
from .issues import issue, active_issues
from .parsing.parsing_seals import compare_seal_text_strict, normalize_seal_text_strict
from .seal_serial import serial_matching_texts

SHAPES = {"round": "圆形", "ellipse": "椭圆", "rectangle": "矩形"}


def requirement_key(value):
    return normalize_seal_text_strict(str(value or ""))


def _registration_preferences(result, candidates):
    """Hide only proven remote duplicates in the picker, not in OCR evidence."""
    local_urls = set()
    variants = (result.get("recognition_variants") or {}).get("seal") or []
    for variant in variants:
        details = variant.get("details") or {}
        check = details.get("seal_check") or {}
        if check.get("region_source") == "清瞳印章区域":
            local_urls.update(a.get("original_url") for a in
                             (details.get("processing_artifacts") or {}).get("seals") or []
                             if a.get("source") != "清瞳印章区域")
    # Older single-engine results may not carry recognition_variants.
    check = result.get("seal_check") or {}
    if not variants and any(c.get("region_source") == "清瞳印章区域" for c in
                            [check, check.get("local_channel") or {}, check.get("local_evidence") or {}]):
        local_urls.update(c["original_url"] for c in candidates if c["source"] == "local")
    preferred = {c["index"] for c in candidates if c["source"] == "local"
                 and c["can_register"] and c["original_url"] in local_urls}
    for candidate in candidates:
        candidate["registration_hidden"] = candidate["source"] == "qingtong" and candidate["index"] in preferred


def seal_candidates(result):
    """Never combine text or shape across artifacts/physical stamp regions."""
    requirement = str((result.get("fields") or {}).get("签章要求") or "")
    remote = ((result.get("seal_check") or {}).get("dual_check") or {}).get("candidates") or []
    candidates, seen = [], set()
    for artifact in (result.get("processing_artifacts") or {}).get("seals") or []:
        if not isinstance(artifact, dict) or artifact.get("role") == "发货单位章":
            continue
        original = str(artifact.get("original_url") or "")
        clean = str(artifact.get("color_isolated_oriented_url") or artifact.get("color_isolated_url") or "")
        identity = original or clean
        if not identity or identity in seen:
            continue
        seen.add(identity)
        shape = artifact.get("shape_code") or next(
            (key for key, label in SHAPES.items() if label == artifact.get("shape")), ""
        )
        texts = []
        ring, center = str(artifact.get("unwrapped_text") or ""), str(artifact.get("round_type_band_text") or "")
        if ring and center:
            texts.append(ring + center)
        texts.extend([artifact.get("rectangular_text"), ring, center, artifact.get("color_isolated_text"),
                      artifact.get("combined_text")])
        texts.extend(artifact.get("matching_texts") or [])
        texts.extend(read.get("text") for read in artifact.get("reads") or []
                     if isinstance(read, dict) and read.get("matching") is not False
                     and read.get("channel") != "serial")
        texts.extend(serial_matching_texts(requirement, artifact))
        texts.extend(artifact.get("color_isolated_texts") or [])
        # Remote OCR belongs only to the explicitly identified API stamp.
        if artifact.get("source") == "清瞳印章区域":
            entry = next((c for c in remote if c.get("index") == artifact.get("api_index")), {})
            texts.append((entry.get("ocr") or {}).get("text"))
        texts = list(dict.fromkeys(str(t).strip() for t in texts if t and str(t).strip()))
        comparison = compare_seal_text_strict(requirement, texts, mismatch_over_partial=True)
        candidates.append({
            "id": hashlib.sha256(identity.encode()).hexdigest()[:24],
            "source": "qingtong" if artifact.get("source") == "清瞳印章区域" else "local",
            "index": artifact.get("index", len(candidates)),
            "image_url": clean, "original_url": original,
            "shape": shape if shape in SHAPES else "", "shape_label": SHAPES.get(shape, "未知形状"),
            "color": str(artifact.get("color") or ""),
            "recognized": comparison["recognized"], "texts": texts,
            "text_status": comparison["status"], "score": comparison["score"],
            "text_match_message": comparison["message"] if comparison.get("field_comparison") else "",
            "can_register": bool(clean and shape in SHAPES and artifact.get("color") in {"red", "blue"}),
        })
    _registration_preferences(result, candidates)
    return candidates


def standard_comparison(result, standard):
    candidates = seal_candidates(result)
    for candidate in candidates:
        candidate["shape_status"] = ("尚无参照" if standard is None else "未识别" if not candidate["shape"]
                                     else "匹配" if candidate["shape"] == standard["shape"] else "不匹配")
        candidate["color_status"] = ("尚无参照" if standard is None else "一致" if candidate["color"] == standard["color"] else "不同")
        candidate["matched"] = candidate["text_status"] == "匹配" and candidate["shape_status"] == "匹配"
    selected = max(candidates, key=lambda c: (c["matched"], c["text_status"] == "匹配", c["score"]), default=None)
    return {
        "standard": standard, "candidates": candidates,
        "selected_id": selected["id"] if selected else None,
        "status": "matched" if selected and selected["matched"] else "missing" if standard is None else "unmatched",
        "requirement": str((result.get("fields") or {}).get("签章要求") or ""),
    }


def apply_standard_check(result, standard, *, preserve_confirmed=True):
    """Apply automatic checks; explicit human conclusions remain authoritative."""
    result = deepcopy(result)
    comparison = standard_comparison(result, standard)
    result["seal_standard_check"] = comparison
    old = result.get("seal_check") or {}
    acceptance = (result.get("recognition_config") or {}).get("acceptance") or {}
    if (not comparison["requirement"] or acceptance.get("seal_match_mode") == "none"
            or (preserve_confirmed and result.get("review_status") in {"确认通过", "确认不通过"})
            or isinstance(old.get("human_confirmed_match"), bool)
            or result.get("overall") == "识别失败"):
        return result
    selected = next((c for c in comparison["candidates"] if c["id"] == comparison["selected_id"]), None)
    matched = comparison["status"] == "matched"
    if standard is None:
        # Absence of a standard is a separate shape-evidence gap. Do not
        # rewrite the original OCR/text judgement when saving only a note.
        if result.get("overall") == "通过":
            result["overall"] = result["final_result"] = "需人工复核"
        return result
    if matched:
        status, message = "匹配", "已找到一枚文字符合签章要求、形状符合标准章的客户印章"
    elif selected and selected["text_status"] == "匹配" and selected["shape_status"] == "不匹配":
        status, message = "不匹配", "印章文字一致，但形状与标准章不一致"
    else:
        status, message = "需人工复核", "未找到文字与形状同时符合要求的客户印章，请逐枚核对"
    result["seal_check"] = {**old, "status": status, "reliable": matched,
                            "message": message, "comparison_policy": "manual_standard_text_and_shape",
                            "recognized": selected["recognized"] if selected else old.get("recognized", ""),
                            "display_text": selected["recognized"] if selected else old.get("recognized", ""),
                            "standard_id": standard["id"] if standard else None}
    # Replace seal-scoped machine warnings only. Date/provider/field failures
    # are not resolved by registering or finding a matching seal.
    issues = [i for i in result.get("review_issues", [issue("review_required", "document", m)
               for m in result.get("review_reasons", [])])
              if i.get("scope") != "seal" or i.get("code") == "provider_failure"]
    if not matched:
        issues.append(issue("seal_standard", "seal", message))
    result["review_issues"] = active_issues(issues, acceptance)
    result["review_reasons"] = list(dict.fromkeys(i["message"] for i in result["review_issues"]))
    result["overall"] = decide_overall(result.get("date_check") or {}, result["seal_check"],
        result["review_reasons"], acceptance, result.get("signature_check"), review_issues=result["review_issues"])
    result["final_result"] = result["overall"]
    return result
