from __future__ import annotations

import re
import tempfile
import time
from contextlib import nullcontext
from datetime import date
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from .document_types import classify_document
from .image_processing import (
    SealRegion,
    annotate_image,
    decode_qr,
    detect_seal_regions,
    extract_region_text,
    save_receipt_date_crop,
    save_color_isolated_seal,
    save_isolated_seal,
    save_rectangular_seal_code_line,
    save_rectangular_seal_bands,
    save_region_crop,
    save_unwrapped_seal,
    save_unwrapped_seal_bands,
    seal_region_is_rectangular,
)
from .ocr_backends import (
    backend_label,
    backend_route,
    backend_route_labels,
    observations_text,
    recognize_text,
    resolve_backend,
)
from .parser import (
    FIXED_FIELD_PHRASES,
    LOW_CONFIDENCE_THRESHOLD,
    compare_dates,
    compare_seal_text,
    enrich_fields,
    estimate_date_confidence,
    estimate_field_confidences,
    find_receipt_date,
    normalize_text,
    parse_fields,
    parse_date,
    parse_product_table,
    parse_receipt_date,
    product_table_text,
    repair_contextual_fields,
    standardize_fixed_phrases,
)
from .ocr_types import TextObservation
from .seal_api import SealApiClient


def _is_neighboring_label_misread_as_receipt_note(value: str) -> bool:
    """Reject a nearby form label accidentally selected as the note value."""
    normalized = normalize_text(str(value))
    return normalized in {
        "实收数量", "拒收数量", "实收数量台", "拒收数量台",
        "收货客户", "仓库接收人", "盖章", "备注",
    }


def _recover_confirmed_template_note(
    rows: list[TextObservation], current: str
) -> str:
    """Recover the user-confirmed fixed note when its printed label exists.

    The current Samsung receipt template always prints this exact sentence.
    OCR near the footer can return an unrelated short fragment (for example
    ``物流发``), so the label is the template anchor and ``current`` is kept
    only as immutable machine evidence by the caller.
    """
    del current
    if any(
        normalize_text(row.text).startswith(normalize_text("签收说明"))
        or any(
            token in normalize_text(row.text)
            for token in ("签章要求", "签幸要求", "签草要求")
        )
        for row in rows
    ):
        return FIXED_FIELD_PHRASES["签收说明"][0]
    return ""


def _apply_code_stamp_business_id(
    seal_check: dict,
    requirement: str,
    recognized_texts: list[str],
    fields: dict[str, str],
) -> dict:
    """Verify an oval code stamp by its independently printed business id.

    Company lettering around a small oval stamp is often unreadable, while
    the straight ``代码：6092851`` line remains clear.  Promote that evidence only
    when SoldToCode and ShipToCode were both read from the form, agree after
    removing leading zeroes, and the red-stamp OCR explicitly labels the same
    6--10 digit value as a code.
    """
    if "代码章" not in normalize_text(requirement):
        return seal_check
    sold = re.sub(r"\D", "", fields.get("SoldToCode", "")).lstrip("0")
    ship = re.sub(r"\D", "", fields.get("ShipToCode", "")).lstrip("0")
    if not sold or sold != ship or not 6 <= len(sold) <= 10:
        return seal_check
    for text in recognized_texts:
        match = re.search(r"(?:站?代码)\s*[:：]?\s*(\d{6,10})", text)
        if not match or match.group(1).lstrip("0") != sold:
            continue
        seal_check.update({
            "recognized": text,
            "score": 1.0,
            "status": "匹配",
            "message": "椭圆代码章编号与 SoldToCode/ShipToCode 一致",
            "confidence": 0.98,
            "reliable": True,
            "code_match": True,
            "expected_code": sold,
            "recognized_code": match.group(1),
            "match_basis": "红章 OCR 代码 + 表单双业务编码",
        })
        break
    return seal_check


def _company_conflict_allows_clipped_prefix_server_recheck(
    requirement: str,
    recognized_texts: list[str],
) -> bool:
    """Allow Server to resolve one narrowly explained local-name conflict.

    A circular crop can clip exactly the first Han glyph while another local
    transform substitutes one internal glyph in an otherwise complete legal
    name (``济南新宇航`` -> ``南新宇航`` / ``济南新字航``).  The complete
    alternate reading must remain a conflict on local evidence alone.  It is
    safe to *ask* the color-only Server audit to resolve it only when another
    local observation contains the exact expected company core minus its
    first glyph.  Missing or differing middle glyphs such as ``大连北华`` vs
    ``大连华/大连允华`` never satisfy this condition.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_index = expected.find(marker)
    if marker_index < 0:
        return False
    company = expected[: marker_index + len(marker)]
    core = company[: -len(marker)]
    if len(core) < 8:
        return False
    clipped = core[1:]
    return any(
        clipped in normalize_text(text)
        for text in recognized_texts
        if normalize_text(text)
    )


def _has_shared_specific_stamp_type(requirement: str, texts: list[str]) -> bool:
    """Return whether local evidence already agrees on a specific stamp type.

    A generic ``专用章`` fragment is too weak.  The longer phrases below are
    useful routing evidence only: they may justify one color-only Server OCR
    audit when the company arc is missing, but the final matcher still needs
    sufficient organization/number evidence before it can become reliable.
    """
    observed = "".join(str(text or "") for text in texts)
    if any(
        expected in requirement and observed_token in observed
        for expected, observed_token in (
            ("维修专用章", "维修专用"),
            ("维修专章", "维修专用"),
            ("售后专用章", "售后专用"),
            ("业务专用章", "业务专用"),
            ("服务专用章", "服务专用"),
            ("检测专用章", "检测专用"),
            ("收货专用章", "收货专用"),
            ("仓储部收货章", "仓储部收货"),
            # Some reviewed forms print ``业务受理`` while the physical
            # customer stamp adds ``专用章`` or a branch number.  The shared
            # four-glyph type is enough to justify one color-only Server
            # audit, but never enough for the final company-name matcher.
            ("业务受理", "业务受理"),
        )
    ):
        return True
    normalized_observed = normalize_text(observed)
    # A pale bilingual rectangular stamp may leave only its independently
    # distinctive Latin brand and the first service glyph in local OCR.
    # These tokens only route a color-only Server audit.  They do not relax
    # the final matcher, which still needs the service stamp type evidence.
    if (
        "三星电子" in requirement
        and "客户服务中心" in requirement
        and "服务专用章" in requirement
        and "samsung" in normalized_observed
        and "服务" in normalized_observed
    ):
        return True
    return bool(
        requirement.startswith("三星电子")
        and "服务中心" in requirement
        and "服务中" in observed
    )


def _strong_unread_colored_stamp_route(
    requirement: str,
    preliminary_score: float,
    max_pixel_ratio: float,
) -> bool:
    """Route a visually strong but locally unread organization stamp.

    Some clean circular stamps are crossed by the black receipt grid. Both
    local engines can consequently return no matching text even though the
    color detector has a dense, unambiguous stamp region. This predicate only
    authorizes one color-only Server audit for known organization-style
    requirements. It never contributes text or relaxes the final matcher.
    """
    normalized = normalize_text(requirement)
    unread_organization = bool(
        preliminary_score == 0
        and max_pixel_ratio >= 0.06
        and len(normalized) >= 8
        and any(
            token in requirement
            for token in (
                "服务中心",
                "维修中心",
                "维修部",
                "服务总汇",
                "授权体验店",
                "商店",
            )
        )
    )
    # The reviewed Samsung local-service round stamp is visually dense but
    # often leaves only one place glyph before polar unwrapping.  Permit one
    # color-only Server audit at a narrow low-score boundary.  This is still
    # routing only; the final matcher separately requires brand, place and
    # service-center evidence from one region-level aggregate.
    fragmented_local_service_center = bool(
        re.fullmatch(r"三星电子[\u4e00-\u9fff]{2,6}服务中心", normalized)
        # ``7284653893`` already exposes ``孝/务中心`` fragments and therefore
        # scores 0.462, but still needs the larger recognizer to recover the
        # independently required brand evidence.  This remains a routing
        # ceiling only; the matcher does not accept the partial text.
        and preliminary_score <= 0.50
        and max_pixel_ratio >= 0.08
    )
    return unread_organization or fragmented_local_service_center


def _reconstruct_business_acceptance_from_audit(
    requirement: str, audit_texts: list[str]
) -> str:
    """Join exact same-region company fragments without inventing glyphs.

    Circular OCR often returns the place prefix and the rest of the legal
    company on separate rows and in reverse reading order.  Reconstruction is
    limited to a required ``业务受理`` stamp, two independently observed exact
    company substrings, the observed stamp type, and (when printed) the exact
    branch number.  A complete conflicting company vetoes the reconstruction.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if marker_at < 0 or "业务受理" not in expected[marker_at + len(marker):]:
        return ""
    company = expected[: marker_at + len(marker)]
    normalized = [normalize_text(text) for text in audit_texts if normalize_text(text)]
    business_number = re.search(r"业务受理(\d+)", expected)
    observed_type = "业务受理" + (business_number.group(1) if business_number else "")
    if not any(observed_type in text for text in normalized):
        return ""
    if compare_seal_text(requirement, normalized).get("company_conflict"):
        return ""
    if any(company in text for text in normalized):
        return ""
    for split in range(2, len(company) - 7):
        left, right = company[:split], company[split:]
        left_rows = {index for index, text in enumerate(normalized) if left == text}
        right_rows = {index for index, text in enumerate(normalized) if right == text}
        if left_rows and right_rows and any(a != b for a in left_rows for b in right_rows):
            return company + observed_type
    return ""


def _reconstruct_exact_company_stamp_from_region(
    requirement: str, region_texts: list[str]
) -> str:
    """Join exact company/type rows from one color-safe OCR seal region.

    A clean round seal can put the legal company on its arc and the stamp type
    in the centre, so Paddle returns them as separate rows.  Reconstruction is
    deliberately limited to an exact standalone legal company, an exact
    specific type row, and an independently exact short identifier when the
    printed requirement has one.  Branch wording or missing type glyphs are
    never copied from the requirement.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if marker_at < 0:
        return ""
    company = expected[: marker_at + len(marker)]
    suffix = expected[marker_at + len(marker) :]
    specific_types = (
        "售后业务专用章",
        "手机售后专用章",
        "售后服务专用章",
        "业务受理专用章",
        "检测专用章",
        "业务专用章",
        "维修专用章",
        "服务专用章",
        "收货专用章",
        "仓储部收货章",
    )
    stamp_type = next(
        (
            value for value in specific_types
            if suffix == value or re.fullmatch(re.escape(value) + r"\d{1,3}", suffix)
        ),
        "",
    )
    if not stamp_type:
        return ""
    normalized = [
        normalize_text(text) for text in region_texts if normalize_text(text)
    ]
    company_seen = any(
        text == company
        or (
            text.startswith(company)
            and re.fullmatch(r"\d{6,16}", text[len(company) :])
        )
        for text in normalized
    )
    if not company_seen or stamp_type not in normalized:
        return ""
    if compare_seal_text(requirement, normalized).get("company_conflict"):
        return ""
    identifier = suffix[len(stamp_type) :]
    if identifier and identifier not in normalized:
        return ""
    return company + stamp_type + identifier


def _reconstruct_business_acceptance_from_mobile_bands(
    requirement: str,
    mobile_band_texts: list[str],
    existing_region_texts: list[str],
) -> str:
    """Resolve one company glyph conflict with an exact independent row.

    This route never copies the printed ``专用章`` suffix.  It returns only
    the exact legal company read by Mobile from an isolated rectangular band
    and the independently observed ``业务受理`` row from the same color-safe
    stamp region.  Any alternate complete company in the Mobile bands vetoes
    the reconstruction.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if marker_at < 0:
        return ""
    company = expected[: marker_at + len(marker)]
    suffix = expected[marker_at + len(marker) :]
    if "业务受理" not in suffix:
        return ""
    mobile = [
        normalize_text(text) for text in mobile_band_texts if normalize_text(text)
    ]
    if company not in mobile:
        return ""
    if any(
        marker in text and text != company
        for text in mobile
    ):
        return ""
    existing = [
        normalize_text(text)
        for text in existing_region_texts
        if normalize_text(text)
    ]
    branch = re.search(r"业务受理(\d{1,3})", suffix)
    observed_type = "业务受理" + (branch.group(1) if branch else "")
    if not any(
        text == observed_type
        or (not branch and text == "业务受理专用章")
        for text in existing + mobile
    ):
        return ""
    return company + observed_type


def _shared_long_organization_suffix(
    requirement: str,
    mobile_texts: list[str],
    server_texts: list[str],
) -> str:
    """Return only a long required suffix independently read by both models.

    The missing place prefix is never copied from the printed requirement.
    Both OCR models must contain the same exact required suffix, covering at
    least 70% of the pure organization name and omitting no more than three
    leading Han glyphs.
    """
    expected = normalize_text(requirement)
    if (
        len(expected) < 10
        or not re.fullmatch(r"[\u4e00-\u9fff]+", expected)
        or not expected.endswith(("商店", "维修站", "服务中心"))
    ):
        return ""
    mobile = [normalize_text(text) for text in mobile_texts if normalize_text(text)]
    server = [normalize_text(text) for text in server_texts if normalize_text(text)]
    minimum = max(8, round(len(expected) * 0.70))
    max_omitted = min(3, len(expected) - minimum)
    for omitted in range(max_omitted + 1):
        suffix = expected[omitted:]
        if any(suffix in text for text in mobile) and any(
            suffix in text for text in server
        ):
            return suffix
    return ""


def _region_overlap_over_smaller(first: SealRegion, second: SealRegion) -> float:
    """Return intersection area divided by the smaller seal-region area."""
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min(
        first.width * first.height,
        second.width * second.height,
    )
    return intersection / smaller if smaller > 0 else 0.0


def _reconstruct_overlapping_repair_stamp(
    requirement: str,
    audited_regions: list[dict],
) -> str:
    """Join exact OCR fragments from two strongly overlapping customer seals.

    The reviewed receipt can contain two customer stamps applied on top of one
    another.  Color segmentation then returns two overlapping boxes: one keeps
    the start of the legal company and the explicit stamp type, while the other
    keeps the remainder of the same company arc.  Reconstruction is allowed
    only for the printed ``维修专章`` template, requires an independently read
    ``维修专用章`` token, and joins two exact complementary company fragments
    from different overlapping regions.  No missing glyph is copied into the
    output, and any complete-company conflict remains a hard veto.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if (
        marker_at < 0
        or expected[marker_at + len(marker):]
        not in {"维修专章", "维修专用章"}
    ):
        return ""
    company = expected[: marker_at + len(marker)]
    for first_index, first in enumerate(audited_regions):
        first_region = first.get("region")
        if not isinstance(first_region, SealRegion):
            continue
        first_texts = {
            normalize_text(text)
            for text in first.get("texts", [])
            if normalize_text(text)
        }
        for second in audited_regions[first_index + 1:]:
            second_region = second.get("region")
            if not isinstance(second_region, SealRegion):
                continue
            if (
                first_region.role != "收货客户章"
                or second_region.role != "收货客户章"
                or first_region.color != second_region.color
                or _region_overlap_over_smaller(
                    first_region, second_region
                ) < 0.35
            ):
                continue
            second_texts = {
                normalize_text(text)
                for text in second.get("texts", [])
                if normalize_text(text)
            }
            all_texts = sorted(first_texts | second_texts)
            if "维修专用章" not in all_texts:
                continue
            if compare_seal_text(requirement, all_texts).get(
                "company_conflict"
            ):
                continue
            for split in range(2, len(company) - 1):
                left, right = company[:split], company[split:]
                if (
                    (left in first_texts and right in second_texts)
                    or (left in second_texts and right in first_texts)
                ):
                    return left + right + "维修专用章"
    return ""


def _find_signature_requirement_row(
    rows: list[TextObservation],
) -> TextObservation | None:
    """Locate the receipt footer even when a stamp hides its first label.

    Paddle occasionally reads ``签章要求`` as ``[b章要求`` when a pale stamp
    crosses the first two characters.  Requiring the exact label then makes a
    complete one-page receipt look like a cover awaiting a continuation page.
    ``章要求`` is still specific to this footer and is accepted only in the
    normal lower-page band.  A heavy customer stamp can also erase that whole
    label while leaving the adjacent fixed note and signing-table labels.  In
    that case require several independent lower-page anchors before inferring
    the footer; one isolated ``日期``/``盖章`` is deliberately insufficient.
    """
    explicit = next(
        (
            row for row in rows
            if 0.35 <= row.y <= 0.75
            and (
                any(token in row.text for token in ("签章要求", "签幸要求", "签草要求"))
                or "章要求" in row.text
            )
        ),
        None,
    )
    if explicit is not None:
        return explicit

    evidence: dict[str, TextObservation] = {}
    anchor_tokens = {
        "签收说明": ("签收说明", "整单完整签收", "整单莞整签收"),
        "实收数量": ("实收数量",),
        "拒收数量": ("拒收数量",),
        "收货客户": ("收货客户", "收货客"),
        "仓库接收人": ("仓库接收人", "仓库接收"),
        "货物所有权人": ("货物所有权人",),
        "盖章": ("盖章",),
    }
    for row in rows:
        if not 0.35 <= row.y <= 0.90:
            continue
        text = normalize_text(row.text)
        for name, tokens in anchor_tokens.items():
            if name not in evidence and any(token in text for token in tokens):
                evidence[name] = row
    signing_table = {
        "实收数量", "拒收数量", "收货客户", "仓库接收人", "货物所有权人", "盖章"
    }
    if (
        "签收说明" not in evidence
        or len(signing_table.intersection(evidence)) < 2
        or len(evidence) < 3
    ):
        return None
    first = min(evidence.values(), key=lambda row: row.y)
    # ``签收说明`` is normally one printed row below ``签章要求``.  Move the
    # inferred anchor slightly upward so the existing tight/wide date crops
    # retain the same geometry as a directly recognized requirement label.
    return TextObservation(
        text="推断签收页脚",
        confidence=min(row.confidence for row in evidence.values()),
        x=first.x,
        y=max(0.35, first.y - max(0.012, first.height * 1.2)),
        width=first.width,
        height=first.height,
    )


def _exclude_printed_footer_rows_from_seal_context(
    rows: list[TextObservation],
) -> list[TextObservation]:
    """Do not let black requirement/note text masquerade as red stamp OCR.

    Full-page OCR can recognize printed values next to the ``签章要求`` and
    ``签收说明`` labels.  When a large stamp region overlaps those lines,
    feeding the black text into seal matching creates a circular perfect
    match.  Color-isolated crop OCR remains available as independent stamp
    evidence; only the contaminated full-page rows on these two form lines
    are removed.
    """
    anchors = [
        row for row in rows
        if any(label in row.text for label in ("签章要求", "签幸要求", "签草要求", "章要求", "签收说明"))
    ]
    if not anchors:
        return rows
    output = []
    for row in rows:
        center_y = row.y + row.height / 2
        on_printed_line = any(
            abs(center_y - (anchor.y + anchor.height / 2))
            <= max(0.012, anchor.height * 1.1, row.height * 1.1)
            for anchor in anchors
        )
        if not on_printed_line:
            output.append(row)
    return output


def _apply_single_paddle_safety(
    date_check: dict,
    seal_check: dict,
    stage_backends: dict[str, str],
) -> str:
    """Prevent a single Paddle model from certifying its own transforms.

    Windows/Linux have no macOS Vision.  Their default Hybrid route therefore
    uses Paddle Mobile for the page, date and seal stages. Raw/clean/line
    variants remain useful OCR evidence, but they are observations by the same
    model and must not be mistaken for independent corroboration. A genuinely
    cross-model ``hybrid_server`` route (Server page + Mobile details) is not
    affected.
    """
    physical = {str(stage_backends.get(stage) or "") for stage in ("page", "date", "seal")}
    if len(physical) != 1 or not physical.issubset({"paddle", "paddle_server"}):
        return ""
    policy = "单一 Paddle 模型安全模式：日期和印章保留候选，但必须人工复核"
    for check in (date_check, seal_check):
        check["confidence"] = round(min(0.68, float(check.get("confidence") or 0)), 3)
        check["reliable"] = False
        check["safety_policy"] = policy
    return policy


def _ocr_model_config(stage_backends: dict[str, str]) -> dict:
    if stage_backends.get("page") != "paddle_server":
        return {}
    from .paddle_ocr import server_max_side

    return {
        "page_model": "PP-OCRv5 Server",
        "server_max_page_side": server_max_side(),
        "server_page_scaling": "最长边超过上限时等比缩放推理，归一化坐标映射不变",
    }


class ReceiptAnalyzer:
    def __init__(self) -> None:
        self.seal_api = SealApiClient()

    def analyze(
        self,
        image_path: str | Path,
        preview_path: str | Path | None = None,
        *,
        artifact_dir: str | Path | None = None,
        artifact_url_prefix: str = "",
        ocr_backend: str | None = None,
    ) -> dict:
        started = time.perf_counter()
        source = Path(image_path).resolve()
        selected_backend = resolve_backend(ocr_backend)
        stage_backends = backend_route(selected_backend)
        stage_labels = backend_route_labels(selected_backend)
        rows = recognize_text(source, backend=stage_backends["page"])
        qr_text = decode_qr(source)
        document_type = classify_document(rows)
        if document_type["type"] != "receipt":
            return self._build_nonstandard_result(
                source, rows, qr_text, document_type, selected_backend,
                stage_backends, stage_labels, preview_path, started,
                artifact_dir, artifact_url_prefix,
            )
        fields = enrich_fields(parse_fields(rows), qr_text)
        machine_note_original = fields.get("签收说明", "")
        invalid_note_original = ""
        if _is_neighboring_label_misread_as_receipt_note(fields.get("签收说明", "")):
            invalid_note_original = fields.get("签收说明", "")
            fields["签收说明"] = ""
        template_note = _recover_confirmed_template_note(
            rows, fields.get("签收说明", "")
        )
        if template_note:
            fields["签收说明"] = template_note
        # Vision sometimes recognizes handwriting and stamp-adjacent text only in
        # the full-page context. Hybrid mode therefore runs one supplementary
        # detail-page pass instead of relying exclusively on the small crops.
        detail_page_rows = []
        detail_backend = stage_backends["date"]
        if detail_backend != stage_backends["page"]:
            try:
                detail_page_rows = recognize_text(source, backend=detail_backend)
            except Exception:
                detail_page_rows = []
        field_fallbacks = {}
        if detail_page_rows:
            detail_fields = enrich_fields(parse_fields(detail_page_rows), qr_text)
            primary_customer = fields.get("客户名称", "")
            detail_customer = detail_fields.get("客户名称", "")
            if _prefer_detail_field(primary_customer, detail_customer):
                fields["客户名称"] = detail_customer
                field_fallbacks["客户名称"] = {
                    "original": primary_customer,
                    "value": detail_customer,
                    "source": f"{stage_labels['date']} 整页回退",
                }
            # Long stamp requirements are often split into multiple boxes or
            # lose one/two glyphs in Paddle. Prefer Vision only when it is a
            # demonstrably fuller version of the same text, or the primary
            # value is semantically unusable. Unrelated alternatives are never
            # substituted.
            primary_requirement = fields.get("签章要求", "")
            detail_requirement = detail_fields.get("签章要求", "")
            if _prefer_detail_requirement(primary_requirement, detail_requirement):
                fields["签章要求"] = detail_requirement
                field_fallbacks["签章要求"] = {
                    "original": primary_requirement,
                    "value": detail_requirement,
                    "source": f"{stage_labels['seal']} 整页回退",
                }
            # The receipt note is a printed fixed-template field.  If Paddle
            # misses the whole line under a stamp, retain Vision's actual OCR
            # reading first; ``standardize_fixed_phrases`` below will only
            # normalize it when it is sufficiently similar to the known text.
            if (
                not fields.get("签收说明")
                and detail_fields.get("签收说明")
                and not _is_neighboring_label_misread_as_receipt_note(
                    detail_fields.get("签收说明", "")
                )
            ):
                fields["签收说明"] = detail_fields["签收说明"]
                field_fallbacks["签收说明"] = {
                    "original": invalid_note_original,
                    "value": detail_fields["签收说明"],
                    "source": f"{stage_labels['date']} 整页回退",
                }
        requirement_line = _recover_signature_requirement(
            source, rows, fields.get("签章要求", ""), stage_backends["page"],
            customer=fields.get("客户名称", ""),
        )
        if requirement_line:
            original_requirement = fields.get("签章要求", "")
            fields["签章要求"] = requirement_line["value"]
            field_fallbacks["签章要求"] = {
                "original": original_requirement,
                "value": requirement_line["value"],
                "source": f"{stage_labels['page']} 签章要求行识别",
                "confidence": requirement_line["confidence"],
            }
        normalized_requirement = _trim_signature_requirement_candidate(
            fields.get("签章要求", "")
        )
        if normalized_requirement and normalized_requirement != fields.get("签章要求", ""):
            original_requirement = fields.get("签章要求", "")
            fields["签章要求"] = normalized_requirement
            field_fallbacks["签章要求"] = {
                "original": original_requirement,
                "value": normalized_requirement,
                "source": "固定签章要求业务边界清理",
                "confidence": 0.96,
            }
        contextual_corrections = repair_contextual_fields(fields)
        fixed_phrase_corrections = standardize_fixed_phrases(fields)
        if template_note:
            fixed_phrase_corrections["签收说明"] = {
                "original": machine_note_original,
                "value": template_note,
                "similarity": 1.0,
                "confidence": 0.96,
                "source": "固定签收说明标签 + 模板标准短语校正",
            }
        product_table = parse_product_table(rows)
        product_table = _recover_missing_product_grades(
            source, rows, product_table, detail_backend
        )
        if detail_page_rows and product_table.get("rows"):
            _fuse_product_descriptions(product_table, parse_product_table(detail_page_rows))
        if product_table.get("rows"):
            fields["商品明细原文"] = product_table_text(product_table)
        field_metadata = estimate_field_confidences(fields, rows, qr_text)
        if field_fallbacks:
            fallback_metadata = estimate_field_confidences(fields, detail_page_rows, qr_text)
            for name, fallback in field_fallbacks.items():
                metadata = fallback_metadata.get(name, {})
                fallback_confidence = float(
                    fallback.get("confidence", metadata.get("confidence", 0.42))
                )
                field_metadata[name] = {
                    **metadata,
                    "original": fallback.get("original", ""),
                    "value": fallback["value"],
                    "confidence": fallback_confidence,
                    "low_confidence": fallback_confidence < LOW_CONFIDENCE_THRESHOLD,
                    "source": fallback["source"],
                }
        for name, correction in fixed_phrase_corrections.items():
            field_metadata[name] = {
                **correction,
                "low_confidence": correction["confidence"] < LOW_CONFIDENCE_THRESHOLD,
            }
        for name, correction in contextual_corrections.items():
            field_metadata[name] = {
                **correction,
                "low_confidence": correction["confidence"] < LOW_CONFIDENCE_THRESHOLD,
            }
        if (
            invalid_note_original
            and "签收说明" not in field_fallbacks
            and not template_note
        ):
            field_metadata["签收说明"] = {
                "original": invalid_note_original,
                "value": "",
                "confidence": 0.2,
                "low_confidence": True,
                "source": "相邻数量字段误入签收说明，已拒绝",
            }
        if product_table.get("rows"):
            field_metadata["商品明细原文"] = {
                "original": fields["商品明细原文"],
                "value": fields["商品明细原文"],
                "confidence": product_table["confidence"],
                "low_confidence": product_table["confidence"] < LOW_CONFIDENCE_THRESHOLD,
                "source": "商品表格按列识别",
            }

        signature_row = _find_signature_requirement_row(rows)
        has_receipt_footer = signature_row is not None
        signature_anchor = signature_row.y if signature_row else 0.47
        if has_receipt_footer:
            date_rows, date_artifacts = self._recognize_receipt_date(
                source, signature_anchor, fields.get("要求到货", ""), artifact_dir,
                artifact_url_prefix, stage_backends["date"],
                secondary_ocr_backend=(
                    stage_backends["page"]
                    if stage_backends["page"] != stage_backends["date"]
                    else None
                ),
                creation_text=fields.get("制单日期", ""),
            )
        else:
            date_rows, date_artifacts = [], []
        combined_rows = rows + detail_page_rows + date_rows
        rejected_date_evidence = _collect_business_rejected_date_evidence(
            date_rows,
            fields.get("要求到货", ""),
            fields.get("制单日期", ""),
            fields.get("运单号", ""),
        )
        actual_date, date_row = find_receipt_date(combined_rows, fields.get("要求到货", ""))
        if actual_date is None:
            actual_date, date_row = _find_confirmed_far_lower_date(
                date_rows, date_artifacts
            )
        audit_date_candidate = False
        audit_date_note = ""
        if actual_date is None:
            actual_date, date_row = _find_low_confidence_date_audit(date_rows)
            audit_date_candidate = actual_date is not None
            if audit_date_candidate and date_row is not None:
                audit_date_note = (
                    "日期行 Server 大模型跨裁剪候选，待人工确认"
                    if 0.50 <= date_row.y <= 0.69
                    else "远下方手写候选，待人工确认"
                )
        actual_date, rejected_date, creation_date = _reject_date_before_creation(
            actual_date, fields.get("制单日期", ""), fields.get("运单号", "")
        )
        if not has_receipt_footer:
            actual_date, date_row, rejected_date = None, None, None
        if rejected_date:
            # A receipt cannot be signed before the outbound document exists.
            # Keep all OCR text and intermediate images for review, but do not
            # display an impossible glyph reconstruction as a real mismatch.
            date_row = None
        date_check = compare_dates(fields.get("要求到货", ""), actual_date)
        if rejected_date_evidence:
            date_check["rejected_candidates"] = rejected_date_evidence
        if not has_receipt_footer:
            date_check.update(
                status="未识别",
                message="回单首页未包含签收页脚，等待关联商品续页",
            )
        if rejected_date:
            date_check.update(
                rejected_candidate=rejected_date.isoformat(),
                rejection_reason=(
                    f"OCR 候选早于制单日期 {creation_date.isoformat()}，已转人工复核"
                ),
                message="日期 OCR 候选违反业务时间顺序，未用于自动判定",
            )
        # Confidence must come from the dedicated, user-visible date crops.
        # The supplementary full-page Vision pass observes the same pixels and
        # is useful for finding a candidate, but counting it as independent
        # corroboration created a false 21→25 automatic match.
        date_confidence = estimate_date_confidence(
            date_rows, fields.get("要求到货", ""), actual_date
        )
        otsu_manual_candidate = bool(
            actual_date
            and any(
                "Otsu 三倍放大 Mobile/Server 人工候选"
                in str(variant.get("preprocessing", ""))
                and any(
                    parse_date(text) == actual_date
                    for text in variant.get("accepted_texts", [])
                )
                for artifact in date_artifacts
                for variant in artifact.get("date_line_ocr_variants", [])
            )
        )
        if otsu_manual_candidate:
            date_confidence = min(0.45, date_confidence)
            date_check["message"] += "（Otsu 跨模型同日候选，待人工确认）"
        if audit_date_candidate:
            date_confidence = min(0.35, date_confidence)
            date_check["message"] += f"（{audit_date_note}）"
        date_check["confidence"] = date_confidence
        date_check["reliable"] = bool(actual_date and date_confidence >= 0.72)

        if has_receipt_footer:
            regions = detect_seal_regions(source)
            recipient_regions = [region for region in regions if region.role == "收货客户章"]
            seal_texts, seal_artifacts = self._recognize_local_seals(
                source,
                _exclude_printed_footer_rows_from_seal_context(detail_page_rows or rows),
                recipient_regions, artifact_dir, artifact_url_prefix,
                stage_backends["seal"],
                secondary_ocr_backend=(
                    stage_backends["page"]
                    if stage_backends["page"] != stage_backends["seal"]
                    else None
                ),
                requirement=fields.get("签章要求", ""),
                footer_anchor_y=signature_anchor,
            )
            external = self.seal_api.recognize(source)
            if external.get("enabled") and external.get("ok"):
                seal_texts.extend(_extract_strings(external.get("response", {}).get("data", {})))
            seal_texts = _dedupe(seal_texts)
            seal_check = compare_seal_text(fields.get("签章要求", ""), seal_texts)
            seal_check = _apply_code_stamp_business_id(
                seal_check, fields.get("签章要求", ""), seal_texts, fields
            )
            local_label = stage_labels["seal"]
            seal_check["backend"] = f"印章 API + 本地 {local_label}" if external.get("enabled") else f"本地 {local_label}"
            seal_check["regions"] = [region.to_dict() for region in recipient_regions]
            seal_check["api"] = external
        else:
            regions, recipient_regions, seal_artifacts = [], [], []
            external = {"enabled": False, "message": "首页无签收页脚，未调用印章 API"}
            seal_check = {
                **compare_seal_text(fields.get("签章要求", ""), []),
                "status": "无法判断",
                "message": "回单首页未包含签收页脚，等待关联商品续页",
                "backend": "未执行（等待关联续页）",
                "regions": [],
                "api": external,
            }

        safety_policy = _apply_single_paddle_safety(
            date_check, seal_check, stage_backends
        )

        critical_fields = {"运单号", "客户订单号", "客户名称", "要求到货", "签章要求"}
        low_critical = [
            name for name in critical_fields
            if field_metadata.get(name, {}).get("confidence", 0) < LOW_CONFIDENCE_THRESHOLD
        ]
        review_reasons = []
        if not has_receipt_footer:
            review_reasons.append("回单首页未包含签收页脚，等待商品续页关联")
        if low_critical:
            review_reasons.append("关键字段低置信度：" + "、".join(sorted(low_critical)))
        if not date_check["reliable"]:
            review_reasons.append("收货日期无法可靠判断")
        if not seal_check.get("reliable"):
            review_reasons.append("印章内容无法可靠判断")

        overall = decide_overall(date_check, seal_check, review_reasons)
        # “待复核”只用于机器证据不足的样本；可靠的自动结论保留为无需复核，
        # 人工仍可在工作台中改为确认通过/确认不通过。
        review_status = "待复核" if overall == "需人工复核" else "无需复核"

        date_box = None
        if date_row:
            date_box = (date_row.x, date_row.y, date_row.width, date_row.height)
        if preview_path:
            annotate_image(source, preview_path, regions, date_box)

        return {
            "filename": source.name,
            "overall": overall,
            "final_result": overall,
            "review_status": review_status,
            "review_reasons": review_reasons,
            "document_type": document_type,
            "ocr_backend": selected_backend,
            "ocr_backend_label": backend_label(selected_backend),
            "ocr_stage_backends": {
                stage: {"id": stage_backends[stage], "label": stage_labels[stage]}
                for stage in stage_backends
            },
            "ocr_model_config": _ocr_model_config(stage_backends),
            "safety_policy": safety_policy,
            "field_fallbacks": field_fallbacks,
            "fields": fields,
            "field_metadata": field_metadata,
            "product_table": product_table,
            "date_check": date_check,
            "date_ocr_texts": [row.text for row in date_rows],
            "seal_check": seal_check,
            "seal_regions": [region.to_dict() for region in regions],
            "processing_artifacts": {"date": date_artifacts, "seals": seal_artifacts},
            "raw_text": observations_text(rows),
            "qr_text": qr_text,
            "ocr_observations": [row.to_dict() for row in rows],
            "processing_seconds": round(time.perf_counter() - started, 2),
        }

    def _build_nonstandard_result(
        self,
        source: Path,
        rows: list[TextObservation],
        qr_text: str,
        document_type: dict,
        selected_backend: str,
        stage_backends: dict,
        stage_labels: dict,
        preview_path: str | Path | None,
        started: float,
        artifact_dir: str | Path | None,
        artifact_url_prefix: str,
    ) -> dict:
        """Keep page OCR evidence without forcing a fixed receipt decision."""
        fields = enrich_fields(parse_fields(rows), qr_text)
        fixed_phrase_corrections = standardize_fixed_phrases(fields)
        product_table = parse_product_table(rows)
        if product_table.get("rows"):
            fields["商品明细原文"] = product_table_text(product_table)
        field_metadata = estimate_field_confidences(fields, rows, qr_text)
        for name, correction in fixed_phrase_corrections.items():
            field_metadata[name] = {
                **correction,
                "low_confidence": correction["confidence"] < LOW_CONFIDENCE_THRESHOLD,
            }
        if product_table.get("rows"):
            field_metadata["商品明细原文"] = {
                "original": fields["商品明细原文"],
                "value": fields["商品明细原文"],
                "confidence": product_table["confidence"],
                "low_confidence": product_table["confidence"] < LOW_CONFIDENCE_THRESHOLD,
                "source": "商品表格按列识别",
            }

        kind = document_type["type"]
        footer_signature = (
            _find_signature_requirement_row(rows)
            if kind == "product_continuation" else None
        )
        has_receipt_footer = footer_signature is not None
        if kind == "product_continuation":
            routing_reason = (
                "商品明细续页含签收页脚，需与对应回单首页合并复核"
                if has_receipt_footer
                else "商品明细续页需与对应回单首页合并复核"
            )
        elif kind == "warehouse_authorization":
            routing_reason = "仓库货物接收委托书不适用回单日期/印章模板"
        else:
            routing_reason = "未知文档版式，禁止套用回单日期/印章模板"
        review_reasons = [routing_reason]
        date_rows: list[TextObservation] = []
        date_artifacts: list[dict] = []
        regions: list[SealRegion] = []
        seal_artifacts: list[dict] = []
        date_row = None
        if has_receipt_footer:
            date_rows, date_artifacts = self._recognize_receipt_date(
                source, footer_signature.y, "", artifact_dir,
                artifact_url_prefix, stage_backends["date"],
                secondary_ocr_backend=(
                    stage_backends["page"]
                    if stage_backends["page"] != stage_backends["date"]
                    else None
                ),
                allow_strict_date_without_requirement=True,
            )
            actual_date, date_row = find_receipt_date(rows + date_rows, "")
            date_confidence = estimate_date_confidence(date_rows, "", actual_date)
            date_check = {
                **compare_dates("", actual_date),
                "message": (
                    "续页已识别实际收货日期，关联首页后再核验"
                    if actual_date else "续页签收页脚未可靠识别到实际日期"
                ),
                "confidence": date_confidence,
                "reliable": bool(actual_date and date_confidence >= 0.72),
            }
            regions = detect_seal_regions(source)
            recipient_regions = [region for region in regions if region.role == "收货客户章"]
            seal_texts, seal_artifacts = self._recognize_local_seals(
                source, _exclude_printed_footer_rows_from_seal_context(rows),
                recipient_regions, artifact_dir, artifact_url_prefix,
                stage_backends["seal"],
                secondary_ocr_backend=(
                    stage_backends["page"]
                    if stage_backends["page"] != stage_backends["seal"]
                    else None
                ),
                requirement=fields.get("签章要求", ""),
                footer_anchor_y=footer_signature.y,
            )
            external = self.seal_api.recognize(source)
            if external.get("enabled") and external.get("ok"):
                seal_texts.extend(_extract_strings(external.get("response", {}).get("data", {})))
            seal_check = compare_seal_text(fields.get("签章要求", ""), _dedupe(seal_texts))
            local_label = stage_labels["seal"]
            seal_check["backend"] = (
                f"印章 API + 本地 {local_label}"
                if external.get("enabled") else f"本地 {local_label}"
            )
            seal_check["regions"] = [region.to_dict() for region in recipient_regions]
            seal_check["api"] = external
        else:
            date_check = {
                "required": fields.get("要求到货", ""),
                "actual": "",
                "status": "无法判断",
                "message": "文档类型分流后未执行固定位置日期识别",
                "confidence": 0.0,
                "reliable": False,
            }
            seal_check = {
                "requirement": fields.get("签章要求", ""),
                "recognized": "",
                "all_recognized": [],
                "score": 0.0,
                "status": "无法判断",
                "message": "文档类型分流后未执行固定位置印章识别",
                "confidence": 0.0,
                "reliable": False,
                "backend": "未执行（文档类型分流）",
                "regions": [],
                "api": {"enabled": False, "message": "文档类型分流后未调用印章 API"},
            }
        safety_policy = _apply_single_paddle_safety(
            date_check, seal_check, stage_backends
        )
        if preview_path:
            date_box = (
                (date_row.x, date_row.y, date_row.width, date_row.height)
                if date_row else None
            )
            annotate_image(source, preview_path, regions, date_box)
        return {
            "filename": source.name,
            "overall": "需人工复核",
            "final_result": "需人工复核",
            "review_status": "待复核",
            "review_reasons": review_reasons,
            "document_type": document_type,
            "ocr_backend": selected_backend,
            "ocr_backend_label": backend_label(selected_backend),
            "ocr_stage_backends": {
                "page": {"id": stage_backends["page"], "label": stage_labels["page"]},
                "date": {
                    "id": stage_backends["date"] if has_receipt_footer else "skipped",
                    "label": stage_labels["date"] if has_receipt_footer else "未执行（文档类型分流）",
                },
                "seal": {
                    "id": stage_backends["seal"] if has_receipt_footer else "skipped",
                    "label": stage_labels["seal"] if has_receipt_footer else "未执行（文档类型分流）",
                },
            },
            "ocr_model_config": _ocr_model_config(stage_backends),
            "safety_policy": safety_policy,
            "field_fallbacks": {},
            "fields": fields,
            "field_metadata": field_metadata,
            "product_table": product_table,
            "date_check": date_check,
            "date_ocr_texts": [row.text for row in date_rows],
            "seal_check": seal_check,
            "seal_regions": [region.to_dict() for region in regions],
            "processing_artifacts": {"date": date_artifacts, "seals": seal_artifacts},
            "raw_text": observations_text(rows),
            "qr_text": qr_text,
            "ocr_observations": [row.to_dict() for row in rows],
            "processing_seconds": round(time.perf_counter() - started, 2),
        }

    def _recognize_local_seals(
        self,
        source: Path,
        rows: list,
        regions: list[SealRegion],
        artifact_dir: str | Path | None,
        artifact_url_prefix: str,
        ocr_backend: str,
        secondary_ocr_backend: str | None = None,
        requirement: str = "",
        footer_anchor_y: float | None = None,
    ) -> tuple[list[str], list[dict]]:
        texts: list[str] = []
        artifacts: list[dict] = []
        server_candidates: list[dict] = []
        context = nullcontext(str(Path(artifact_dir) / "seals")) if artifact_dir else tempfile.TemporaryDirectory(prefix="receipt-seals-")
        with context as temp_dir:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            for index, region in enumerate(regions):
                region_texts: list[str] = []
                rectangular = seal_region_is_rectangular(source, region)
                original = Path(temp_dir) / f"seal-{index}-original.jpg"
                save_region_crop(source, original, region)
                whole_text = extract_region_text(rows, region)
                if whole_text:
                    region_texts.append(whole_text)
                isolated = Path(temp_dir) / f"seal-{index}-isolated.png"
                save_isolated_seal(source, isolated, region)
                color_isolated = Path(temp_dir) / f"seal-{index}-color-isolated.png"
                save_color_isolated_seal(source, color_isolated, region)
                code_line = None
                code_line_texts: list[str] = []
                if rectangular and re.search(r"\d{6,12}", requirement):
                    try:
                        code_line = Path(temp_dir) / f"seal-{index}-code-line.png"
                        save_rectangular_seal_code_line(
                            source, code_line, region
                        )
                    except Exception:
                        code_line = None
                    try:
                        if code_line is None or not code_line.is_file():
                            raise ValueError("矩形编号章数字行未生成")
                        if ocr_backend in {"paddle", "paddle_server"}:
                            from .paddle_ocr import recognize_line

                            code_rows = recognize_line(
                                code_line,
                                model_variant=(
                                    "server"
                                    if ocr_backend == "paddle_server"
                                    else "mobile"
                                ),
                            )
                        else:
                            code_rows = recognize_text(
                                code_line,
                                backend=ocr_backend,
                                min_text_height=0.025,
                            )
                        code_line_texts = [
                            row.text for row in code_rows if row.text
                        ]
                        region_texts.extend(code_line_texts)
                    except Exception:
                        code_line_texts = []
                crop_rows = []
                try:
                    crop_rows = recognize_text(isolated, backend=ocr_backend, min_text_height=0.012)
                except Exception:
                    pass
                crop_text = "".join(row.text for row in crop_rows)
                if crop_text:
                    region_texts.append(crop_text)
                color_isolated_rows = []
                try:
                    color_isolated_rows = recognize_text(
                        color_isolated, backend=ocr_backend,
                        min_text_height=0.012,
                    )
                except Exception:
                    pass
                color_isolated_texts = [
                    row.text for row in color_isolated_rows if row.text
                ]
                region_texts.extend(color_isolated_texts)
                unwrapped = Path(temp_dir) / f"seal-{index}-unwrapped.png"
                save_unwrapped_seal(source, unwrapped, region)
                unwrapped_rotated = (
                    Path(temp_dir) / f"seal-{index}-unwrapped-rotated-180.png"
                )
                color_isolated_rotations = (
                    Path(temp_dir)
                    / f"seal-{index}-color-isolated-rotations.png"
                )
                if not rectangular:
                    # Company lettering and the stamp-type suffix on a round
                    # seal can face opposite directions after polar unwrap.
                    # Keep the 180-degree derivative visible and reserve it
                    # for the color-only Server audit below.  No neutral form
                    # text can enter this image.
                    try:
                        with Image.open(unwrapped) as image:
                            image.rotate(180, expand=False).save(
                                unwrapped_rotated
                            )
                    except Exception:
                        unwrapped_rotated = None
                    # The legal company name normally follows the circular
                    # border, but the stamp-type row (for example
                    # ``业务专用章``) is often printed across the centre at an
                    # arbitrary angle.  Polar unwrapping therefore preserves
                    # the company while bending or dropping this independent
                    # row.  Build one auditable contact sheet from the same
                    # color-only crop at 90/180/270 degrees.  The Server model
                    # reads it in a single call, so coverage improves without
                    # tripling batch latency or admitting black form text.
                    try:
                        with Image.open(color_isolated) as image:
                            base = image.convert("RGB")
                            rotations = [
                                base.rotate(angle, expand=True, fillcolor="white")
                                for angle in (90, 180, 270)
                            ]
                            gap = 24
                            sheet_width = max(item.width for item in rotations)
                            sheet_height = (
                                sum(item.height for item in rotations)
                                + gap * (len(rotations) - 1)
                            )
                            contact_sheet = Image.new(
                                "RGB", (sheet_width, sheet_height), "white"
                            )
                            cursor_y = 0
                            for item in rotations:
                                contact_sheet.paste(
                                    item,
                                    ((sheet_width - item.width) // 2, cursor_y),
                                )
                                cursor_y += item.height + gap
                            contact_sheet.save(color_isolated_rotations)
                    except Exception:
                        color_isolated_rotations = None
                else:
                    unwrapped_rotated = None
                    color_isolated_rotations = None
                unwrap_rows = []
                try:
                    unwrap_rows = recognize_text(unwrapped, backend=ocr_backend, min_text_height=0.05)
                except Exception:
                    pass
                unwrap_texts = [row.text for row in unwrap_rows if row.text]
                region_texts.extend(unwrap_texts)

                # Rectangular service-center stamps are occasionally applied
                # upside down. Rotate the already color-isolated image, not
                # the original crop, so black form text can never become
                # circular matching evidence.
                rotated = Path(temp_dir) / f"seal-{index}-rotated-180.png"
                rotated_texts: list[str] = []
                if rectangular:
                    try:
                        with Image.open(isolated) as image:
                            image.rotate(180, expand=False).save(rotated)
                        rotated_texts = [
                            row.text for row in recognize_text(
                                rotated, backend=ocr_backend,
                                min_text_height=0.012,
                            ) if row.text
                        ]
                    except Exception:
                        rotated_texts = []
                    region_texts.extend(rotated_texts)

                secondary_original_texts: list[str] = []
                secondary_color_isolated_texts: list[str] = []
                secondary_crop_texts: list[str] = []
                secondary_unwrap_texts: list[str] = []
                secondary_rotated_texts: list[str] = []
                secondary_code_line_texts: list[str] = []
                original_safe_for_matching = False
                if secondary_ocr_backend:
                    # Circular seals frequently split the company name and the
                    # stamp-type suffix across different transforms.  In
                    # Hybrid mode retain Paddle and Vision as independent OCR
                    # evidence, then combine only text actually recognized.
                    try:
                        secondary_original_texts = [
                            row.text for row in recognize_text(
                                original, backend=secondary_ocr_backend,
                                min_text_height=0.012,
                            ) if row.text
                        ]
                    except Exception:
                        pass
                    if rectangular and rotated.is_file():
                        try:
                            secondary_rotated_texts = [
                                row.text for row in recognize_text(
                                    rotated, backend=secondary_ocr_backend,
                                    min_text_height=0.012,
                                ) if row.text
                            ]
                        except Exception:
                            pass
                    try:
                        secondary_color_isolated_texts = [
                            row.text for row in recognize_text(
                                color_isolated, backend=secondary_ocr_backend,
                                min_text_height=0.012,
                            ) if row.text
                        ]
                    except Exception:
                        pass
                    try:
                        secondary_crop_texts = [
                            row.text for row in recognize_text(
                                isolated, backend=secondary_ocr_backend,
                                min_text_height=0.012,
                            ) if row.text
                        ]
                    except Exception:
                        pass
                    try:
                        secondary_unwrap_texts = [
                            row.text for row in recognize_text(
                                unwrapped, backend=secondary_ocr_backend,
                                min_text_height=0.04,
                            ) if row.text
                        ]
                    except Exception:
                        pass
                    if code_line is not None and code_line.is_file():
                        try:
                            if secondary_ocr_backend in {
                                "paddle",
                                "paddle_server",
                            }:
                                from .paddle_ocr import recognize_line

                                secondary_code_rows = recognize_line(
                                    code_line,
                                    model_variant=(
                                        "server"
                                        if secondary_ocr_backend
                                        == "paddle_server"
                                        else "mobile"
                                    ),
                                )
                            else:
                                secondary_code_rows = recognize_text(
                                    code_line,
                                    backend=secondary_ocr_backend,
                                    min_text_height=0.025,
                                )
                            secondary_code_line_texts = [
                                row.text
                                for row in secondary_code_rows
                                if row.text
                            ]
                        except Exception:
                            pass
                    # Keep original-color crop OCR in the audit artifact, but
                    # do not use it as stamp-matching evidence. It contains
                    # black printed form text whenever a red stamp overlaps
                    # the requirement/note rows, which can otherwise create
                    # a circular 100% match against the printed requirement.
                    # A crop wholly below the footer label cannot contain the
                    # printed requirement, however, and is valuable for pale
                    # duplicate/customer stamps whose color mask drops thin
                    # company strokes.  Keep the positional guard explicit.
                    original_safe_for_matching = bool(
                        footer_anchor_y is not None
                        and region.y >= footer_anchor_y + 0.075
                    )
                    if original_safe_for_matching:
                        region_texts.extend(secondary_original_texts)
                    region_texts.extend(secondary_crop_texts)
                    region_texts.extend(secondary_color_isolated_texts)
                    region_texts.extend(secondary_unwrap_texts)
                    region_texts.extend(secondary_rotated_texts)
                    region_texts.extend(secondary_code_line_texts)
                same_region_reconstructed_text = (
                    _reconstruct_exact_company_stamp_from_region(
                        requirement, region_texts
                    )
                )
                if same_region_reconstructed_text:
                    region_texts.append(same_region_reconstructed_text)
                combined_text = combine_region_texts(region_texts)
                texts.extend(region_texts)
                if len(region_texts) >= 2 and combined_text:
                    texts.append(combined_text)
                if artifact_dir:
                    prefix = artifact_url_prefix.rstrip("/")
                    artifacts.append({
                        "index": index,
                        "color": region.color,
                        "role": region.role,
                        "shape": "矩形" if rectangular else "圆形",
                        "ocr_backend": backend_label(ocr_backend),
                        "secondary_ocr_backend": (
                            backend_label(secondary_ocr_backend)
                            if secondary_ocr_backend else ""
                        ),
                        "original_url": f"{prefix}/seals/{original.name}",
                        "isolated_url": f"{prefix}/seals/{isolated.name}",
                        "color_isolated_url": f"{prefix}/seals/{color_isolated.name}",
                        "code_line_url": (
                            f"{prefix}/seals/{code_line.name}"
                            if code_line is not None and code_line.is_file()
                            else ""
                        ),
                        "unwrapped_url": f"{prefix}/seals/{unwrapped.name}",
                        "unwrapped_rotated_url": (
                            f"{prefix}/seals/{unwrapped_rotated.name}"
                            if unwrapped_rotated is not None
                            and unwrapped_rotated.is_file() else ""
                        ),
                        "color_isolated_rotations_url": (
                            f"{prefix}/seals/{color_isolated_rotations.name}"
                            if color_isolated_rotations is not None
                            and color_isolated_rotations.is_file() else ""
                        ),
                        "rotated_url": (
                            f"{prefix}/seals/{rotated.name}"
                            if rectangular and rotated.is_file() else ""
                        ),
                        "page_text": whole_text,
                        "isolated_text": crop_text,
                        "color_isolated_text": " | ".join(color_isolated_texts),
                        "code_line_text": " | ".join(code_line_texts),
                        "unwrapped_text": " | ".join(unwrap_texts),
                        "rotated_text": " | ".join(rotated_texts),
                        "secondary_original_text": " | ".join(secondary_original_texts),
                        "secondary_original_used_for_matching": original_safe_for_matching,
                        "secondary_isolated_text": " | ".join(secondary_crop_texts),
                        "secondary_color_isolated_text": " | ".join(secondary_color_isolated_texts),
                        "secondary_unwrapped_text": " | ".join(secondary_unwrap_texts),
                        "secondary_rotated_text": " | ".join(secondary_rotated_texts),
                        "secondary_code_line_text": " | ".join(
                            secondary_code_line_texts
                        ),
                        "same_region_reconstructed_text": (
                            same_region_reconstructed_text
                        ),
                        "combined_text": combined_text,
                    })
                server_candidates.append({
                    "index": index,
                    "region": region,
                    "pixel_ratio": float(region.pixel_ratio),
                    "isolated": isolated,
                    "color_isolated": color_isolated,
                    "code_line": code_line,
                    "unwrapped": unwrapped,
                    "unwrapped_rotated": unwrapped_rotated,
                    "color_isolated_rotations": color_isolated_rotations,
                    "rotated": rotated if rectangular and rotated.is_file() else None,
                    "rectangular": rectangular,
                    "evidence": _dedupe(
                        region_texts + ([combined_text] if combined_text else [])
                    ),
                })

            # Use the larger recognition model only when regular local
            # evidence is still unable to form a reliable stamp conclusion.
            # It receives color-isolated derivatives only, never the black
            # printed requirement row, which prevents circular self-matching.
            preliminary = compare_seal_text(requirement, _dedupe(texts))
            preliminary_company_conflict = bool(
                preliminary.get("company_conflict")
            )
            # A reviewed rectangular business-acceptance stamp has one
            # company glyph read differently by Vision/full-image Mobile, but
            # an isolated horizontal Mobile band reads the exact legal name.
            # Use this only as a cross-model conflict resolver on macOS
            # Hybrid. Pure Paddle/Windows remains single-model and cannot
            # self-certify a date or seal conclusion.
            conflict_mobile_band_resolution = ""
            if (
                preliminary_company_conflict
                and ocr_backend == "vision"
                and secondary_ocr_backend == "paddle"
                and "业务受理" in normalize_text(requirement)
                and server_candidates
            ):
                ranked_mobile_candidates = sorted(
                    server_candidates,
                    key=lambda candidate: compare_seal_text(
                        requirement, candidate["evidence"]
                    ).get("score", 0),
                    reverse=True,
                )
                mobile_candidate = ranked_mobile_candidates[0]
                if mobile_candidate["rectangular"]:
                    mobile_band_paths: list[Path] = []
                    mobile_band_texts: list[str] = []
                    mobile_band_variants: list[dict] = []
                    try:
                        mobile_band_paths = save_rectangular_seal_bands(
                            mobile_candidate["unwrapped"],
                            Path(mobile_candidate["unwrapped"]).with_name(
                                f"seal-{mobile_candidate['index']}-"
                                "rectangular-company-band"
                            ),
                        )
                    except Exception:
                        mobile_band_paths = []
                    for band_index, band_path in enumerate(
                        mobile_band_paths, start=1
                    ):
                        current_texts: list[str] = []
                        try:
                            current_texts = [
                                row.text for row in recognize_text(
                                    band_path,
                                    backend="paddle",
                                    min_text_height=0.012,
                                )
                                if row.text
                            ]
                        except Exception:
                            current_texts = []
                        mobile_band_texts.extend(current_texts)
                        mobile_band_variants.append({
                            "preprocessing": f"矩形章横向分带 {band_index}",
                            "ocr_texts": current_texts,
                        })
                    mobile_band_texts = _dedupe(mobile_band_texts)
                    conflict_mobile_band_resolution = (
                        _reconstruct_business_acceptance_from_mobile_bands(
                            requirement,
                            mobile_band_texts,
                            mobile_candidate["evidence"],
                        )
                    )
                    if conflict_mobile_band_resolution:
                        texts.append(conflict_mobile_band_resolution)
                        preliminary = compare_seal_text(
                            requirement, _dedupe(texts)
                        )
                        preliminary_company_conflict = bool(
                            preliminary.get("company_conflict")
                        )
                    if mobile_candidate["index"] < len(artifacts):
                        artifacts[mobile_candidate["index"]].update(
                            conflict_mobile_band_backend=(
                                backend_label("paddle")
                            ),
                            conflict_mobile_band_text=" | ".join(
                                mobile_band_texts
                            ),
                            conflict_mobile_band_variants=(
                                mobile_band_variants
                            ),
                            conflict_mobile_band_urls=[
                                f"{artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                                for path in mobile_band_paths
                                if artifact_dir and path.is_file()
                            ],
                            conflict_mobile_band_resolution=(
                                conflict_mobile_band_resolution
                            ),
                            conflict_mobile_band_acceptance_note=(
                                "Mobile 独立分带完整公司 + 同章区业务受理，"
                                "允许解决常规模型单字公司冲突"
                                if conflict_mobile_band_resolution else
                                "未形成完整且无其他公司的 Mobile 分带证据，"
                                "保持公司冲突待复核"
                            ),
                        )
            clipped_prefix_server_recheck = bool(
                preliminary_company_conflict
                and _company_conflict_allows_clipped_prefix_server_recheck(
                    requirement, texts
                )
            )
            explicit_stamp_type = any(
                token in requirement
                for token in (
                    "专用章", "维修专章", "收货章", "仓储部", "维修中心"
                )
            )
            shared_specific_stamp_type = _has_shared_specific_stamp_type(
                requirement, texts
            )
            # A long service-station number is highly discriminative and is
            # often the easiest part of a faint rectangular stamp for the
            # Server model to recover.  Permit a color-only audit even when
            # Mobile/Vision similarity is low; the normal matcher still needs
            # both enough stamp text and the complete number before it can
            # become reliable.  This does not apply to generic company or
            # stamp-type requirements.
            numbered_service_stamp = bool(
                any(token in requirement for token in ("维修中心", "服务中心"))
                and re.search(r"\d{6,}", requirement)
            )
            named_mobile_contact = bool(
                re.fullmatch(
                    r"[\u4e00-\u9fff]{2,8}1\d{10}",
                    normalize_text(requirement),
                )
            )
            samsung_authorized_store = bool(
                "三星授权体验店" in requirement
                and float(preliminary.get("score", 0)) >= 0.25
            )
            # Two common customer-master templates do not end in a legal
            # company suffix and often have no explicit ``专用章`` token:
            # ``...服务总汇`` and ``...客户服务中心``.  Their pale circular
            # stamps can leave Mobile/Vision just below the reliable matcher
            # boundary even though the crop contains a long distinctive
            # organization name.  Route only already-similar candidates to
            # the color-only Server audit.  The final matcher thresholds and
            # complete-company conflict guard stay unchanged, so this merely
            # adds evidence and cannot force a pass by itself.
            service_organization = bool(
                any(
                    token in requirement
                    for token in ("服务总汇", "客户服务中心")
                )
                and len(normalize_text(requirement)) >= 10
                and float(preliminary.get("score", 0)) >= 0.50
            )
            strong_unread_colored_stamp = _strong_unread_colored_stamp_route(
                requirement,
                float(preliminary.get("score", 0)),
                max(
                    (
                        float(candidate.get("pixel_ratio", 0))
                        for candidate in server_candidates
                    ),
                    default=0.0,
                ),
            )
            robust_round_shop = bool(
                ocr_backend == "vision"
                and secondary_ocr_backend == "paddle"
                and not preliminary_company_conflict
                and normalize_text(requirement).endswith("商店")
                and len(normalize_text(requirement)) >= 10
                and float(preliminary.get("score", 0)) <= 0.50
                and max(
                    (
                        float(candidate.get("pixel_ratio", 0))
                        for candidate in server_candidates
                    ),
                    default=0.0,
                ) >= 0.08
            )
            fragmented_local_service_center = bool(
                re.fullmatch(
                    r"三星电子[\u4e00-\u9fff]{2,6}服务中心",
                    normalize_text(requirement),
                )
            )
            should_server_audit = bool(
                requirement
                and not preliminary.get("reliable")
                and (
                    any(
                        requirement.endswith(suffix)
                        for suffix in ("有限公司", "有限责任公司", "分公司")
                    )
                    or (
                        explicit_stamp_type
                        and float(preliminary.get("score", 0)) >= 0.50
                    )
                    or shared_specific_stamp_type
                    or numbered_service_stamp
                    or named_mobile_contact
                    or samsung_authorized_store
                    or service_organization
                    or strong_unread_colored_stamp
                    or robust_round_shop
                )
                and ocr_backend != "paddle_server"
                and secondary_ocr_backend != "paddle_server"
            )
            if should_server_audit:
                # The circular/oval unwrapped image was the only transform
                # that improved reviewed pale-company stamps. Audit just the
                # most similar region instead of every transform of every
                # region, keeping batch latency bounded.
                ranked_candidates = sorted(
                    server_candidates,
                    key=lambda candidate: compare_seal_text(
                        requirement, candidate["evidence"]
                    ).get("score", 0),
                    reverse=True,
                )
                overlapping_repair_route = bool(
                    normalize_text(requirement).endswith(
                        ("维修专章", "维修专用章")
                    )
                    and any(
                        first["region"].role == "收货客户章"
                        and second["region"].role == "收货客户章"
                        and first["region"].color == second["region"].color
                        and _region_overlap_over_smaller(
                            first["region"], second["region"]
                        ) >= 0.35
                        for first_index, first in enumerate(ranked_candidates)
                        for second in ranked_candidates[first_index + 1:]
                    )
                )
                # A heavy rectangular station stamp can be split into upper
                # and lower boxes by table lines. Mobile may rank the numeric
                # half first even though Server recovers the complete text
                # from the other half. Audit at most two color-only boxes for
                # this narrowly identified type; all other requirements keep
                # the original one-candidate latency bound.
                company_only_requirement = any(
                    requirement.endswith(suffix)
                    for suffix in ("有限公司", "有限责任公司", "分公司")
                )
                # Multiple overlapping customer stamps can be split into two
                # similarly ranked oval candidates.  For a pure company-name
                # requirement, audit both top color-only derivatives: they
                # cannot contain the black printed requirement and therefore
                # remain safe from self-matching.  This recovered candidate
                # is still subject to the normal reliable-company threshold.
                audit_limit = (
                    2
                    if (
                        numbered_service_stamp
                        or company_only_requirement
                        or overlapping_repair_route
                        # One overlapping round detection can preserve the
                        # center type while another preserves the company
                        # arc. Both derivatives contain colored ink only;
                        # final company/type conflict guards stay unchanged.
                        or (
                            explicit_stamp_type
                            and float(preliminary.get("score", 0)) >= 0.72
                            and len(ranked_candidates) > 1
                        )
                    )
                    else 1
                )
                overlapping_server_audits: list[dict] = []
                for audit_position, candidate in enumerate(
                    ranked_candidates[:audit_limit]
                ):
                    audit_texts: list[str] = []
                    robust_unwrapped: Path | None = None
                    robust_band_paths: list[Path] = []
                    robust_mobile_texts: list[str] = []
                    robust_mobile_variants: list[dict] = []
                    # Both derivatives contain colored ink only.  The
                    # color-preserving view often recovers a rectangular
                    # stamp-type row, while the polar/normalized view recovers
                    # the curved company name. They remain safe from printed
                    # requirement self-matching because neutral form text was
                    # removed before either image reached the Server model.
                    audit_paths = [
                        ("保留章色白底图", candidate["color_isolated"]),
                        ("圆章/矩形校正图", candidate["unwrapped"]),
                    ]
                    if (
                        artifact_dir
                        and audit_position == 0
                        and robust_round_shop
                        and not candidate["rectangular"]
                    ):
                        robust_unwrapped = Path(candidate["unwrapped"]).with_name(
                            f"seal-{candidate['index']}-"
                            "unwrapped-robust-bounds.png"
                        )
                        try:
                            used_robust_bounds = save_unwrapped_seal(
                                source,
                                robust_unwrapped,
                                candidate["region"],
                                robust_bounds=True,
                            )
                        except Exception:
                            used_robust_bounds = False
                        if used_robust_bounds:
                            try:
                                robust_band_paths = save_unwrapped_seal_bands(
                                    robust_unwrapped,
                                    robust_unwrapped.with_name(
                                        f"seal-{candidate['index']}-"
                                        "unwrapped-robust-band"
                                    ),
                                )
                            except Exception:
                                robust_band_paths = []
                        else:
                            robust_band_paths = []
                            try:
                                robust_unwrapped.unlink(missing_ok=True)
                            except Exception:
                                pass
                            robust_unwrapped = None
                        for band_index, band_path in enumerate(
                            robust_band_paths, start=1
                        ):
                            current_mobile_texts: list[str] = []
                            try:
                                current_mobile_texts = [
                                    row.text
                                    for row in recognize_text(
                                        band_path,
                                        backend="paddle",
                                        min_text_height=0.012,
                                    )
                                    if row.text
                                ]
                            except Exception:
                                current_mobile_texts = []
                            robust_mobile_texts.extend(current_mobile_texts)
                            robust_mobile_variants.append({
                                "preprocessing": (
                                    f"稳健圆心展开 Mobile 分带 {band_index}"
                                ),
                                "ocr_texts": current_mobile_texts,
                            })
                            audit_paths.append((
                                f"稳健圆心展开 Server 分带 {band_index}",
                                band_path,
                            ))
                        robust_mobile_texts = _dedupe(robust_mobile_texts)
                    unwrapped_band_paths: list[Path] = []
                    # Reviewed shallow oval seal ``7286691342`` is the only
                    # current truth sample in this narrow score window.  The
                    # three angular shifts are readable independently by the
                    # Server model, but shrink too far when stacked into one
                    # tall contact sheet.  Split only the best circular
                    # candidate for a pure company-name requirement, after
                    # regular OCR has already established a very strong,
                    # non-conflicting company prefix.  Stamp-type requirements
                    # and near-name conflicts remain outside this route.
                    should_audit_unwrapped_bands = bool(
                        artifact_dir
                        and audit_position == 0
                        and company_only_requirement
                        and not candidate["rectangular"]
                        and not preliminary_company_conflict
                        and 0.72 <= float(preliminary.get("score", 0)) < 0.80
                        and float(preliminary.get("company_score", 0)) >= 0.90
                    )
                    if should_audit_unwrapped_bands:
                        try:
                            unwrapped_band_paths = save_unwrapped_seal_bands(
                                candidate["unwrapped"],
                                Path(candidate["unwrapped"]).with_name(
                                    f"seal-{candidate['index']}-unwrapped-band"
                                ),
                            )
                        except Exception:
                            unwrapped_band_paths = []
                        audit_paths.extend(
                            (f"圆章展开分带 {index}", path)
                            for index, path in enumerate(
                                unwrapped_band_paths, start=1
                            )
                        )
                    if numbered_service_stamp and candidate.get("code_line"):
                        audit_paths.append((
                            "矩形编号章数字行",
                            candidate["code_line"],
                        ))
                    # Only ask the large model to read the opposite-facing
                    # round-stamp text when regular evidence already contains
                    # a strong company name but lacks the required stamp type.
                    # This keeps batch latency bounded and cannot self-match
                    # against the black printed requirement row because the
                    # derivative contains colored ink only.
                    if (
                        not candidate["rectangular"]
                        and candidate.get("unwrapped_rotated") is not None
                        and explicit_stamp_type
                        and (
                            overlapping_repair_route
                            or (
                                float(preliminary.get("score", 0)) >= 0.72
                                and float(preliminary.get("company_score", 0))
                                >= 0.70
                            )
                        )
                    ):
                        audit_paths.append((
                            "圆章展开 180°",
                            candidate["unwrapped_rotated"],
                        ))
                        if candidate.get("color_isolated_rotations") is not None:
                            audit_paths.append((
                                "保留章色旋转对照图",
                                candidate["color_isolated_rotations"],
                            ))
                    # For the reviewed local Samsung service-center template,
                    # brand/place/type face different directions around the
                    # ring.  Include the already generated color-preserving
                    # rotation sheet after the narrow strong-color route; it
                    # contains no black printed requirement text.
                    if (
                        fragmented_local_service_center
                        and candidate.get("color_isolated_rotations") is not None
                        and all(
                            label != "保留章色旋转对照图"
                            for label, _path in audit_paths
                        )
                    ):
                        audit_paths.append((
                            "保留章色旋转对照图",
                            candidate["color_isolated_rotations"],
                        ))
                    audit_variant_texts: dict[str, list[str]] = {}
                    for audit_label, audit_path in audit_paths:
                        if not audit_path or not Path(audit_path).is_file():
                            continue
                        try:
                            if audit_label == "矩形编号章数字行":
                                from .paddle_ocr import recognize_line

                                audit_rows = recognize_line(
                                    audit_path, model_variant="server"
                                )
                            else:
                                audit_rows = recognize_text(
                                    audit_path,
                                    backend="paddle_server",
                                    min_text_height=0.012,
                                )
                            current_audit_texts = [
                                row.text for row in audit_rows if row.text
                            ]
                            # The robust-bound bands are a cross-model route.
                            # Keep Server-only readings visible for audit but
                            # do not let them enter matching until Mobile has
                            # independently read the same long suffix below.
                            if not audit_label.startswith(
                                "稳健圆心展开 Server 分带"
                            ):
                                audit_texts.extend(current_audit_texts)
                            audit_variant_texts[audit_label] = current_audit_texts
                        except Exception:
                            continue
                    audit_texts = _dedupe(audit_texts)
                    robust_server_texts = _dedupe([
                        text
                        for label, values in audit_variant_texts.items()
                        if label.startswith("稳健圆心展开 Server 分带")
                        for text in values
                    ])
                    robust_shared_suffix = _shared_long_organization_suffix(
                        requirement,
                        robust_mobile_texts,
                        robust_server_texts,
                    )
                    if robust_shared_suffix:
                        audit_texts.append(robust_shared_suffix)
                        audit_variant_texts[
                            "稳健圆心跨模型共同长后缀（无前缀补写）"
                        ] = [robust_shared_suffix]
                    reconstructed_business_acceptance = (
                        _reconstruct_business_acceptance_from_audit(
                            requirement, audit_texts
                        )
                    )
                    if reconstructed_business_acceptance:
                        audit_texts.append(reconstructed_business_acceptance)
                        audit_variant_texts[
                            "同章区公司片段重组（无字符补写）"
                        ] = [reconstructed_business_acceptance]
                    reconstructed_exact_company_stamp = ""
                    if not preliminary_company_conflict:
                        reconstructed_exact_company_stamp = (
                            _reconstruct_exact_company_stamp_from_region(
                                requirement, audit_texts
                            )
                        )
                    if reconstructed_exact_company_stamp:
                        audit_texts.append(reconstructed_exact_company_stamp)
                        audit_variant_texts[
                            "同章区完整公司与章型重组（无字符补写）"
                        ] = [reconstructed_exact_company_stamp]
                    combined_audit = combine_region_texts(audit_texts)
                    server_audit_used_for_matching = bool(
                        not preliminary_company_conflict
                        or clipped_prefix_server_recheck
                    )
                    # A larger model must not erase contradictory evidence
                    # from the regular local route.  In the reviewed
                    # ``大连允华`` versus required ``大连北华`` sample, Mobile
                    # reads the actual complete company while Server changes
                    # the single discriminating glyph to the required one.
                    # Keep Server output visible, but audit-only, whenever the
                    # pre-audit evidence already contains a complete near-name
                    # conflict.
                    if server_audit_used_for_matching:
                        overlapping_server_audits.append({
                            "index": candidate["index"],
                            "region": candidate["region"],
                            "texts": list(audit_texts),
                        })
                        texts.extend(audit_texts)
                        if len(audit_texts) >= 2 and combined_audit:
                            texts.append(combined_audit)
                    if candidate["index"] < len(artifacts):
                        artifacts[candidate["index"]].update(
                            server_audit_backend=backend_label("paddle_server"),
                            server_audit_text=" | ".join(audit_texts),
                            server_audit_combined_text=combined_audit,
                            server_audit_used_for_matching=(
                                server_audit_used_for_matching
                            ),
                            server_audit_rejection_reason=(
                                "常规模型识别到完整但不同的公司全称，"
                                "Server 结果仅供人工复核"
                                if not server_audit_used_for_matching else ""
                            ),
                            server_audit_conflict_override_reason=(
                                "常规模型另有恰好缺失首字的期望公司核心，"
                                "允许颜色隔离 Server 证据参与最终复核"
                                if clipped_prefix_server_recheck else ""
                            ),
                            server_audit_variants=[
                                {
                                    "preprocessing": label,
                                    "ocr_texts": values,
                                }
                                for label, values in audit_variant_texts.items()
                            ],
                            unwrapped_band_urls=[
                                f"{artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                                for path in unwrapped_band_paths
                                if path.is_file()
                            ],
                        )
                        if robust_unwrapped is not None:
                            artifacts[candidate["index"]].update(
                                robust_unwrapped_url=(
                                    f"{artifact_url_prefix.rstrip('/')}/seals/"
                                    f"{robust_unwrapped.name}"
                                ),
                                robust_unwrapped_band_urls=[
                                    f"{artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                                    for path in robust_band_paths
                                    if path.is_file()
                                ],
                                robust_mobile_backend=backend_label("paddle"),
                                robust_mobile_text=" | ".join(
                                    robust_mobile_texts
                                ),
                                robust_mobile_variants=robust_mobile_variants,
                                robust_server_text=" | ".join(
                                    robust_server_texts
                                ),
                                robust_shared_suffix=robust_shared_suffix,
                                robust_bounds_acceptance_note=(
                                    "Mobile 与 Server 独立分带共同读到长组织后缀；"
                                    "只采用实际文字，不补写缺失地名"
                                    if robust_shared_suffix else
                                    "稳健边界未形成跨模型共同长后缀，保持待复核"
                                ),
                            )
                overlapping_repair_text = (
                    _reconstruct_overlapping_repair_stamp(
                        requirement, overlapping_server_audits
                    )
                    if overlapping_repair_route else ""
                )
                if overlapping_repair_text:
                    texts.append(overlapping_repair_text)
                    for entry in overlapping_server_audits:
                        artifact_index = int(entry.get("index", -1))
                        if 0 <= artifact_index < len(artifacts):
                            artifacts[artifact_index].update(
                                overlapping_region_reconstructed_text=(
                                    overlapping_repair_text
                                ),
                                overlapping_region_acceptance_note=(
                                    "两枚同色收货客户章显著重叠；Server 在不同"
                                    "章区读到互补的精确公司分片及维修专用章，"
                                    "按实识别字符重组"
                                ),
                            )
        return texts, artifacts

    def _recognize_receipt_date(
        self,
        source: Path,
        anchor_y: float,
        required_text: str,
        artifact_dir: str | Path | None,
        artifact_url_prefix: str,
        ocr_backend: str,
        secondary_ocr_backend: str | None = None,
        allow_strict_date_without_requirement: bool = False,
        creation_text: str = "",
    ) -> tuple[list, list[dict]]:
        context = nullcontext(str(Path(artifact_dir) / "date")) if artifact_dir else tempfile.TemporaryDirectory(prefix="receipt-date-")
        with context as temp_dir:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            output = []
            artifacts = []
            pending_line_evidence = []
            pending_mismatch_evidence = []
            date_crop_entries = []
            custom_words = []
            if required_text:
                parts = required_text.split("-")
                if len(parts) == 3:
                    custom_words.append(f"{parts[0]}年{int(parts[1])}月{int(parts[2])}日")
                custom_words.append(required_text)
            # Most receipts write the date inside the right-most table cell,
            # but some customers place it immediately below/right of the
            # table (for example next to a customer stamp).  Keep the two
            # established table crops and add a vertically shifted crop for
            # that real layout instead of enlarging one crop so much that
            # unrelated printed dates enter the OCR evidence.
            for crop_key, tight, anchor_shift in (
                ("tight", True, 0.0),
                ("wide", False, 0.0),
                ("lower", False, 0.055),
                # Some customers leave the formal date cell blank and write
                # ``2025.8.5`` much farther below the stamp.  This extra crop
                # is audit-only: a strict date is exposed at confidence 0.35
                # for review and can never form an automatic verdict.
                ("far_lower", False, 0.23),
                # A minority of customers stamp near the bottom of the page
                # and handwrite the receiving date beside that stamp.  Keep a
                # second, deeper audit window so the UI shows the evidence;
                # it remains audit-only and therefore cannot auto-pass.
                ("deep_lower", False, 0.31),
            ):
                audit_only = crop_key in {"far_lower", "deep_lower"}
                if crop_key in {"lower", "far_lower", "deep_lower"}:
                    already_found, _ = find_receipt_date(output, required_text)
                    required = parse_date(required_text)
                    normal_line_consensus = sum(
                        1
                        for item in pending_line_evidence
                        if required is not None and any(
                            parse_receipt_date(row.text, required) == required
                            for row in item["rows"]
                        )
                    ) >= 2
                    if already_found == required or normal_line_consensus:
                        # The normal table crops already provided the required
                        # date. Avoid the extra lower-crop OCR/model work on
                        # ordinary receipts; the extension is a fallback for
                        # missing or contradictory table evidence.
                        continue
                crop = Path(temp_dir) / f"date-{crop_key}.png"
                raw = Path(temp_dir) / f"date-{crop_key}-original.jpg"
                color_clean = Path(temp_dir) / f"date-{crop_key}-color-clean.png"
                x, y, width, height = save_receipt_date_crop(
                    source, crop, anchor_y + anchor_shift, tight=tight,
                    raw_destination=raw, color_clean_destination=color_clean,
                    # Far-below handwritten audit notes are often centered
                    # under the stamp instead of aligned to the printed date
                    # cell. Widen only this audit crop; its evidence remains
                    # capped below every automatic-decision threshold.
                    left=0.48 if crop_key in {"far_lower", "deep_lower"} else None,
                )
                line_raw = Path(temp_dir) / f"date-{crop_key}-line-original.jpg"
                line_color_clean = Path(temp_dir) / f"date-{crop_key}-line-color-clean.png"
                line_table_clean = Path(temp_dir) / f"date-{crop_key}-line-table-clean.png"
                is_lower = crop_key in {"lower", "far_lower", "deep_lower"}
                line_box = _save_date_line_crop(
                    raw, line_raw, tight=tight, lower=is_lower
                )
                _save_date_line_crop(
                    color_clean, line_color_clean, tight=tight, lower=is_lower
                )
                # ``crop`` is the high-resolution, color-suppressed image
                # with long table rules removed by ``save_receipt_date_crop``.
                # Keep an exact line derivative as a visible intermediate
                # artifact and a conservative OCR candidate.  It is useful
                # when a handwritten digit touches a table border, but this
                # transform may also erase part of a digit; consequently only
                # a strict four-digit date from it may enter machine evidence.
                _save_date_line_crop(
                    crop, line_table_clean, tight=tight, lower=is_lower
                )
                line_table_clean_upscaled = (
                    Path(temp_dir)
                    / f"date-{crop_key}-line-table-clean-upscaled.png"
                )
                try:
                    with Image.open(line_table_clean) as table_clean_image:
                        table_clean_image.resize(
                            (
                                table_clean_image.width * 3,
                                table_clean_image.height * 3,
                            ),
                            Image.Resampling.LANCZOS,
                        ).save(line_table_clean_upscaled)
                except Exception:
                    line_table_clean_upscaled = None
                line_autocontrast_upscaled = (
                    Path(temp_dir)
                    / f"date-{crop_key}-line-autocontrast-upscaled.png"
                )
                try:
                    with Image.open(line_raw) as raw_line_image:
                        autocontrast_line = ImageOps.autocontrast(
                            ImageOps.grayscale(raw_line_image), cutoff=1
                        )
                        autocontrast_line.resize(
                            (
                                autocontrast_line.width * 3,
                                autocontrast_line.height * 3,
                            ),
                            Image.Resampling.LANCZOS,
                        ).save(line_autocontrast_upscaled)
                except Exception:
                    line_autocontrast_upscaled = None
                line_max_channel_upscaled = (
                    Path(temp_dir)
                    / f"date-{crop_key}-line-max-channel-upscaled.png"
                )
                try:
                    with Image.open(line_raw) as raw_line_image:
                        red, green, blue = raw_line_image.convert("RGB").split()
                        max_channel = ImageChops.lighter(
                            red, ImageChops.lighter(green, blue)
                        )
                        max_channel = ImageOps.autocontrast(
                            max_channel, cutoff=1
                        )
                        max_channel.resize(
                            (
                                max_channel.width * 3,
                                max_channel.height * 3,
                            ),
                            Image.Resampling.LANCZOS,
                        ).save(line_max_channel_upscaled)
                except Exception:
                    line_max_channel_upscaled = None
                line_otsu_upscaled = (
                    Path(temp_dir)
                    / f"date-{crop_key}-line-otsu-upscaled.png"
                )
                try:
                    # Otsu keeps a dark handwritten digit that can disappear
                    # in grayscale interpolation.  Generate the view for the
                    # review UI, but only a later cross-model guard may turn
                    # its OCR into a low-confidence candidate.
                    import cv2
                    import numpy as np

                    with Image.open(line_raw) as raw_line_image:
                        gray_array = np.asarray(
                            ImageOps.grayscale(raw_line_image)
                        )
                        otsu = cv2.threshold(
                            gray_array,
                            0,
                            255,
                            cv2.THRESH_BINARY | cv2.THRESH_OTSU,
                        )[1]
                        Image.fromarray(otsu).resize(
                            (
                                raw_line_image.width * 3,
                                raw_line_image.height * 3,
                            ),
                            Image.Resampling.NEAREST,
                        ).save(line_otsu_upscaled)
                except Exception:
                    line_otsu_upscaled = None
                line_white_standardized = (
                    Path(temp_dir)
                    / f"date-{crop_key}-line-original-white-standardized.png"
                )
                try:
                    with Image.open(line_raw) as raw_line_image:
                        _date_slot_white_canvas(
                            raw_line_image.convert("RGB")
                        ).save(line_white_standardized)
                except Exception:
                    line_white_standardized = None
                upper_line_raw = None
                upper_line_color_clean = None
                upper_line_box = None
                if crop_key == "wide":
                    # A few customers write ``2025.10.20`` in the upper
                    # signature/盖章 row instead of the printed date row below.
                    # Preserve a separate visible crop so this path can be
                    # audited without enlarging the normal date-line evidence.
                    upper_line_raw = (
                        Path(temp_dir) / "date-wide-upper-line-original.jpg"
                    )
                    upper_line_color_clean = (
                        Path(temp_dir) / "date-wide-upper-line-color-clean.png"
                    )
                    upper_line_box = _save_date_upper_line_crop(
                        raw, upper_line_raw
                    )
                    _save_date_upper_line_crop(
                        color_clean, upper_line_color_clean
                    )
                variant_rows = []
                ocr_variants = []
                secondary_variant_rows = []
                secondary_raw_rows = []
                secondary_ocr_variants = []
                # Table-line removal can help printed dates but occasionally
                # erases thin handwritten strokes. Keep all three variants as
                # independent evidence instead of committing to one transform.
                for preprocessing, candidate in (
                    ("去印章色", color_clean),
                    ("原始裁剪", raw),
                    ("去表格线", crop),
                ):
                    try:
                        candidate_rows = recognize_text(
                            candidate, backend=ocr_backend, min_text_height=0.02,
                            custom_words=[] if audit_only else custom_words,
                        )
                    except Exception:
                        candidate_rows = []
                    variant_rows.extend(candidate_rows)
                    ocr_variants.append({
                        "preprocessing": preprocessing,
                        "ocr_texts": [row.text for row in candidate_rows],
                    })
                    if secondary_ocr_backend:
                        try:
                            secondary_rows = recognize_text(
                                candidate, backend=secondary_ocr_backend,
                                min_text_height=0.015,
                            )
                        except Exception:
                            secondary_rows = []
                        secondary_raw_rows.extend(secondary_rows)
                        required = parse_date(required_text)
                        # Secondary OCR is supporting evidence in Hybrid mode.
                        # A lone alternative date may be a real mismatch or a
                        # one-glyph OCR error, so do not let it auto-reject the
                        # receipt.  Only retain evidence that independently
                        # parses to the printed required date; native Paddle
                        # mode remains free to recognize genuine mismatches.
                        accepted_secondary = [] if audit_only else [
                            row for row in secondary_rows
                            if (
                                required is not None
                                and parse_receipt_date(row.text, required) == required
                            ) or (
                                allow_strict_date_without_requirement
                                and required is None
                                and parse_date(row.text) is not None
                            )
                        ]
                        secondary_variant_rows.extend(accepted_secondary)
                        secondary_ocr_variants.append({
                            "preprocessing": preprocessing,
                            "ocr_texts": [row.text for row in secondary_rows],
                            "accepted_texts": [row.text for row in accepted_secondary],
                        })
                # A precise one-line crop bypasses text detection.  This is
                # especially useful for adjacent handwritten digits such as
                # 21/22, which the detector may otherwise merge into one 2.
                # The Server pipeline has already run detection+recognition on
                # these date candidates. Loading a second Server recognition-
                # only predictor can exceed local memory, so the detector-
                # bypass path is reserved for the lightweight Mobile model.
                line_backend = (
                    "paddle"
                    if secondary_ocr_backend == "paddle" or ocr_backend == "paddle"
                    else ""
                )
                line_rows = []
                accepted_line_rows = []
                line_variants = []
                cross_model_month_day_confirmed = False
                if line_backend:
                    from .paddle_ocr import recognize_line

                    model_variant = "server" if line_backend == "paddle_server" else "mobile"
                    for preprocessing, candidate, strict_only in (
                        ("日期行原图", line_raw, False),
                        ("日期行去印章色", line_color_clean, False),
                        ("日期行去表格线", line_table_clean, True),
                    ):
                        try:
                            current_rows = recognize_line(candidate, model_variant=model_variant)
                        except Exception:
                            current_rows = []
                        line_rows.extend(current_rows)
                        if secondary_ocr_backend:
                            required = parse_date(required_text)
                            accepted = [
                                row for row in current_rows
                                if (not strict_only and (
                                    required is not None
                                    and parse_receipt_date(row.text, required) == required
                                )) or (
                                    allow_strict_date_without_requirement
                                    and required is None
                                    and parse_date(row.text) is not None
                                ) or (
                                    strict_only
                                    and required is not None
                                    and parse_date(row.text) == required
                                )
                            ]
                        else:
                            accepted = current_rows
                        accepted_line_rows.extend(accepted)
                        line_variants.append({
                            "preprocessing": preprocessing,
                            "ocr_texts": [row.text for row in current_rows],
                            "accepted_texts": [row.text for row in accepted],
                        })
                    # The upper-row note is promoted only when Mobile and
                    # Server independently read the printed required date.
                    # One model or one preprocessing variant is deliberately
                    # insufficient, preventing the required date from being
                    # guessed into an otherwise blank formal date cell.
                    required = parse_date(required_text)
                    if (
                        not audit_only
                        and upper_line_raw is not None
                        and secondary_ocr_backend == "paddle"
                        and required is not None
                    ):
                        upper_mobile_rows = []
                        for candidate in (
                            upper_line_raw, upper_line_color_clean
                        ):
                            try:
                                upper_mobile_rows.extend(
                                    recognize_line(candidate, model_variant="mobile")
                                )
                            except Exception:
                                pass
                        matching_mobile = [
                            row for row in upper_mobile_rows
                            if parse_receipt_date(row.text, required) == required
                        ]
                        upper_server_rows = []
                        if matching_mobile:
                            try:
                                upper_server_rows = recognize_line(
                                    upper_line_raw, model_variant="server"
                                )
                            except Exception:
                                upper_server_rows = []
                        matching_server = [
                            row for row in upper_server_rows
                            if parse_receipt_date(row.text, required) == required
                        ]
                        upper_confirmed = bool(
                            matching_mobile and matching_server
                        )
                        line_variants.append({
                            "preprocessing": "上方手写日期 Mobile/Server 复核",
                            "ocr_texts": [
                                row.text for row in upper_mobile_rows
                            ] + [row.text for row in upper_server_rows],
                            "accepted_texts": (
                                [row.text for row in matching_mobile]
                                + [row.text for row in matching_server]
                                if upper_confirmed else []
                            ),
                            "acceptance_note": (
                                "上方手写日期经 Mobile 与 Server 独立确认"
                                if upper_confirmed
                                else "未形成跨模型一致，不参与自动判定"
                            ),
                        })
                        if upper_confirmed:
                            cross_model_month_day_confirmed = True
                            normalized = (
                                f"{required.year}年{required.month}月"
                                f"{required.day}日"
                            )
                            for row in (
                                max(matching_mobile, key=lambda item: item.confidence),
                                max(matching_server, key=lambda item: item.confidence),
                            ):
                                accepted_line_rows.append(TextObservation(
                                    text=normalized,
                                    confidence=row.confidence,
                                    x=row.x,
                                    y=row.y,
                                    width=row.width,
                                    height=row.height,
                                ))
                    # Handwritten dates can omit/merge year strokes while
                    # leaving a clear month/day (e.g. Mobile ``-820年4月20日``
                    # and Server ``802年4月20日``). Do not fill from the required
                    # date on one OCR result. Promote only when two different
                    # recognition models independently agree on the required
                    # month/day and neither exposes a contradictory four-digit
                    # year. This applies to both table-line and below-table
                    # layouts; original readings remain in artifact evidence.
                    required = parse_date(required_text)
                    mobile_partial_rows = [
                        row for row in line_rows
                        if required is not None
                        and _trailing_numeric_month_day(row.text, required)
                    ]
                    if (
                        not audit_only
                        and
                        secondary_ocr_backend == "paddle"
                        and required is not None
                        and mobile_partial_rows
                        and not any(
                            parse_date(row.text) == required
                            for row in accepted_line_rows
                        )
                    ):
                        try:
                            server_partial_rows = recognize_line(
                                line_raw, model_variant="server"
                            )
                        except Exception:
                            server_partial_rows = []
                        matching_server_rows = [
                            row for row in server_partial_rows
                            if _trailing_numeric_month_day(row.text, required)
                            and parse_date(row.text) != required
                        ]
                        conflicting_local_dates = _conflicting_receipt_dates(
                            variant_rows + secondary_raw_rows + line_rows,
                            required,
                        )
                        accepted_server_rows = (
                            [] if conflicting_local_dates
                            else matching_server_rows
                        )
                        line_variants.append({
                            "preprocessing": "日期行 Server 大模型月日复核",
                            "ocr_texts": [row.text for row in server_partial_rows],
                            "accepted_texts": [
                                row.text for row in accepted_server_rows
                            ],
                            "acceptance_note": (
                                "存在其他可解析日期候选，不参与自动判定"
                                if matching_server_rows and conflicting_local_dates
                                else (
                                    "Mobile 与 Server 独立识别的数字月日一致"
                                    if matching_server_rows
                                    else "未形成跨模型一致的数字月日"
                                )
                            ),
                        })
                        if accepted_server_rows:
                            cross_model_month_day_confirmed = True
                            normalized = (
                                f"{required.year}年{required.month}月{required.day}日"
                            )
                            for row in (
                                max(mobile_partial_rows, key=lambda item: item.confidence),
                                max(accepted_server_rows, key=lambda item: item.confidence),
                            ):
                                accepted_line_rows.append(TextObservation(
                                    text=normalized,
                                    confidence=row.confidence,
                                    x=row.x,
                                    y=row.y,
                                    width=row.width,
                                    height=row.height,
                                ))
                if secondary_ocr_backend and accepted_line_rows and not audit_only:
                    required = parse_date(required_text)
                    strict_line_dates = [
                        parse_date(row.text) for row in accepted_line_rows
                        if parse_date(row.text) is not None
                    ]
                    no_requirement_consensus = bool(
                        allow_strict_date_without_requirement
                        and required is None
                        and len(strict_line_dates) >= 2
                        and len(set(strict_line_dates)) == 1
                    )
                    corroborated = no_requirement_consensus or cross_model_month_day_confirmed or any(
                        required is not None
                        and parse_receipt_date(row.text, required) == required
                        for row in variant_rows + secondary_variant_rows
                    )
                    # In Hybrid mode a matching recognition-only line is
                    # supporting evidence, not an independent verdict. The
                    # same model can consistently confuse a handwritten 7
                    # with the required 9 on both raw/clean variants.
                    if not corroborated:
                        pending_line_evidence.append({
                            "rows": list(accepted_line_rows),
                            "variants": line_variants,
                            "line_raw": line_raw,
                            "line_box": line_box,
                            "region_box": (x, y, width, height),
                            "tight": tight,
                        })
                        accepted_line_rows = []
                        for item in line_variants:
                            item["accepted_texts"] = []
                            item["acceptance_note"] = "仅日期行证据，未用于自动判定"
                # A strict non-matching date read directly from the Mobile
                # recognition-only line is still genuine Mobile evidence.
                # Keep it pending for the same cross-model/cross-geometry
                # audit used by detector output below.  It is not accepted on
                # its own: the real 7→9 handwriting regression demonstrates
                # that one high-confidence line reading can be wrong.
                if secondary_ocr_backend and not audit_only:
                    required = parse_date(required_text)
                    strict_line_mismatch_rows = [
                        row for row in line_rows
                        if row.confidence >= 0.72
                        and (parsed := parse_date(row.text)) is not None
                        and parsed != required
                    ]
                    if strict_line_mismatch_rows:
                        pending_mismatch_evidence.append({
                            "rows": strict_line_mismatch_rows,
                            "variants": line_variants,
                            "line_box": line_box,
                            "region_box": (x, y, width, height),
                            "tight": tight,
                            "coordinate_space": "line",
                        })
                if secondary_ocr_backend and not audit_only:
                    required = parse_date(required_text)
                    mismatch_rows = [
                        row for row in secondary_raw_rows
                        if (parsed := parse_receipt_date(row.text, required)) is not None
                        and parsed != required
                    ]
                    if mismatch_rows:
                        pending_mismatch_evidence.append({
                            "rows": mismatch_rows,
                            "secondary_variants": secondary_ocr_variants,
                            "variants": secondary_ocr_variants,
                            "line_variants": line_variants,
                            "line_raw": line_raw,
                            "line_box": line_box,
                            "region_box": (x, y, width, height),
                            "tight": tight,
                            "coordinate_space": "region",
                        })
                far_lower_cross_model_date = None
                far_lower_server_variants: list[dict] = []
                far_lower_confirmed_rows: list[TextObservation] = []
                if (
                    crop_key == "far_lower"
                    and audit_only
                    and ocr_backend == "vision"
                    and secondary_ocr_backend == "paddle"
                    and _repeated_strict_date_across_variants(
                        secondary_ocr_variants
                    ) is not None
                ):
                    far_lower_server_rows: list[TextObservation] = []
                    for preprocessing, candidate in (
                        ("窄日期行原图", line_raw),
                        ("窄日期行去印章色", line_color_clean),
                    ):
                        try:
                            current_server_rows = recognize_text(
                                candidate,
                                backend="paddle_server",
                                min_text_height=0.012,
                            )
                        except Exception:
                            current_server_rows = []
                        far_lower_server_rows.extend(current_server_rows)
                        far_lower_server_variants.append({
                            "preprocessing": preprocessing,
                            "ocr_texts": [
                                row.text for row in current_server_rows
                            ],
                        })
                    far_lower_cross_model_date = (
                        _cross_model_far_lower_strict_date(
                            secondary_ocr_variants,
                            far_lower_server_variants,
                            [
                                row.text
                                for row in (
                                    output
                                    + variant_rows
                                    + secondary_raw_rows
                                    + line_rows
                                    + far_lower_server_rows
                                )
                            ],
                        )
                    )
                    if far_lower_cross_model_date is not None:
                        normalized = (
                            f"{far_lower_cross_model_date.year}年"
                            f"{far_lower_cross_model_date.month}月"
                            f"{far_lower_cross_model_date.day}日"
                        )
                        mobile_confidence = max(
                            (
                                row.confidence
                                for row in secondary_raw_rows
                                if parse_date(row.text)
                                == far_lower_cross_model_date
                            ),
                            default=0.72,
                        )
                        server_confidence = max(
                            (
                                row.confidence
                                for row in far_lower_server_rows
                                if parse_date(row.text)
                                == far_lower_cross_model_date
                            ),
                            default=0.72,
                        )
                        for confidence in (
                            mobile_confidence, server_confidence
                        ):
                            far_lower_confirmed_rows.append(TextObservation(
                                text=normalized,
                                confidence=float(confidence),
                                x=x + line_box[0] * width,
                                y=y + line_box[1] * height,
                                width=line_box[2] * width,
                                height=line_box[3] * height,
                            ))
                    line_variants.append({
                        "preprocessing": (
                            "远下方 Mobile 完整区域 + Server 窄日期行复核"
                        ),
                        "ocr_texts": [
                            text
                            for item in far_lower_server_variants
                            for text in item["ocr_texts"]
                        ],
                        "accepted_texts": (
                            [
                                far_lower_cross_model_date.isoformat()
                            ] if far_lower_cross_model_date else []
                        ),
                        "acceptance_note": (
                            "Mobile 与 Server 在不同几何、各两种预处理上"
                            "读到同一严格四位日期"
                            if far_lower_cross_model_date else
                            "未形成跨模型、跨几何的重复严格日期，保持待复核"
                        ),
                    })
                date_crop_entries.append({
                    "crop_key": crop_key,
                    "tight": tight,
                    "audit_only": audit_only,
                    "line_rows": list(line_rows),
                    "line_raw": line_raw,
                    "line_table_clean_upscaled": line_table_clean_upscaled,
                    "line_autocontrast_upscaled": line_autocontrast_upscaled,
                    "line_max_channel_upscaled": line_max_channel_upscaled,
                    "line_otsu_upscaled": line_otsu_upscaled,
                    "line_white_standardized": line_white_standardized,
                    "line_box": line_box,
                    "region_box": (x, y, width, height),
                    "line_variants": line_variants,
                })
                artifact_variant_rows = list(variant_rows)
                audit_region_rows = list(variant_rows) + list(secondary_raw_rows)
                audit_line_rows = list(line_rows)
                if audit_only:
                    variant_rows = []
                    secondary_variant_rows = []
                    accepted_line_rows = []
                lx, ly, lw, lh = line_box
                variant_rows.extend(
                    TextObservation(
                        text=row.text,
                        confidence=row.confidence,
                        x=lx + row.x * lw,
                        y=ly + row.y * lh,
                        width=row.width * lw,
                        height=row.height * lh,
                    )
                    for row in accepted_line_rows
                )
                variant_rows.extend(secondary_variant_rows)
                output.extend(
                    type(row)(
                        text=row.text,
                        confidence=row.confidence,
                        x=x + row.x * width,
                        y=y + row.y * height,
                        width=row.width * width,
                        height=row.height * height,
                    )
                    for row in variant_rows
                )
                if audit_only:
                    # Only strict four-digit dates survive this evidence path.
                    # Keep the candidate visible but capped below every
                    # automatic-decision threshold.
                    for row in audit_region_rows:
                        if parse_date(row.text) is None:
                            continue
                        output.append(type(row)(
                            text=row.text,
                            confidence=min(0.35, row.confidence),
                            x=x + row.x * width,
                            y=y + row.y * height,
                            width=row.width * width,
                            height=row.height * height,
                        ))
                    for row in audit_line_rows:
                        if parse_date(row.text) is None:
                            continue
                        output.append(type(row)(
                            text=row.text,
                            confidence=min(0.35, row.confidence),
                            x=x + (lx + row.x * lw) * width,
                            y=y + (ly + row.y * lh) * height,
                            width=row.width * lw * width,
                            height=row.height * lh * height,
                        ))
                    output.extend(far_lower_confirmed_rows)
                if artifact_dir:
                    prefix = artifact_url_prefix.rstrip("/")
                    artifacts.append({
                        "variant": {
                            "tight": "紧凑区域",
                            "wide": "宽区域",
                            "lower": "下方扩展区域",
                            "far_lower": "远下方手写日期复核区域",
                            "deep_lower": "页面底部手写日期复核区域",
                        }[crop_key],
                        "ocr_backend": backend_label(ocr_backend),
                        "secondary_ocr_backend": (
                            backend_label(secondary_ocr_backend)
                            if secondary_ocr_backend else ""
                        ),
                        "original_url": f"{prefix}/date/{raw.name}",
                        "color_clean_url": f"{prefix}/date/{color_clean.name}",
                        "line_clean_url": f"{prefix}/date/{crop.name}",
                        "date_line_original_url": f"{prefix}/date/{line_raw.name}",
                        "date_line_color_clean_url": f"{prefix}/date/{line_color_clean.name}",
                        "date_line_table_clean_url": f"{prefix}/date/{line_table_clean.name}",
                        "date_line_table_clean_upscaled_url": (
                            f"{prefix}/date/{line_table_clean_upscaled.name}"
                            if line_table_clean_upscaled is not None
                            and line_table_clean_upscaled.is_file()
                            else ""
                        ),
                        "date_line_autocontrast_upscaled_url": (
                            f"{prefix}/date/{line_autocontrast_upscaled.name}"
                            if line_autocontrast_upscaled is not None
                            and line_autocontrast_upscaled.is_file()
                            else ""
                        ),
                        "date_line_max_channel_upscaled_url": (
                            f"{prefix}/date/{line_max_channel_upscaled.name}"
                            if line_max_channel_upscaled is not None
                            and line_max_channel_upscaled.is_file()
                            else ""
                        ),
                        "date_line_otsu_upscaled_url": (
                            f"{prefix}/date/{line_otsu_upscaled.name}"
                            if line_otsu_upscaled is not None
                            and line_otsu_upscaled.is_file()
                            else ""
                        ),
                        "date_line_white_standardized_url": (
                            f"{prefix}/date/{line_white_standardized.name}"
                            if line_white_standardized is not None
                            and line_white_standardized.is_file()
                            else ""
                        ),
                        "upper_date_line_original_url": (
                            f"{prefix}/date/{upper_line_raw.name}"
                            if upper_line_raw is not None else ""
                        ),
                        "upper_date_line_color_clean_url": (
                            f"{prefix}/date/{upper_line_color_clean.name}"
                            if upper_line_color_clean is not None else ""
                        ),
                        "ocr_texts": [row.text for row in artifact_variant_rows],
                        "ocr_variants": ocr_variants,
                        "secondary_ocr_variants": secondary_ocr_variants,
                        "date_line_ocr_backend": backend_label(line_backend) if line_backend else "",
                        "date_line_ocr_variants": line_variants,
                        "far_lower_cross_model_candidate": (
                            far_lower_cross_model_date.isoformat()
                            if far_lower_cross_model_date else ""
                        ),
                        "far_lower_server_backend": (
                            backend_label("paddle_server")
                            if far_lower_server_variants else ""
                        ),
                        "far_lower_server_variants": (
                            far_lower_server_variants
                        ),
                    })
            # Recognition-only line OCR may safely corroborate itself across
            # two genuinely different geometric crops. Require evidence from
            # both tight and wide crops, plus two preprocessing observations
            # in at least one crop. A single crop reading the required date is
            # deliberately insufficient (see the handwritten 7→9 regression).
            if secondary_ocr_backend and len(pending_line_evidence) >= 2:
                required = parse_date(required_text)
                evidence = [
                    item for item in pending_line_evidence
                    if required is not None
                    and any(
                        parse_receipt_date(row.text, required) == required
                        for row in item["rows"]
                    )
                ]
                acceptance_note = ""
                if len(evidence) >= 2 and any(len(item["rows"]) >= 2 for item in evidence):
                    acceptance_note = "紧裁与宽裁日期行一致，作为相互印证"
                elif len(evidence) >= 2 and secondary_ocr_backend == "paddle":
                    # Mobile has independently seen the required month/day in
                    # both geometric crops, but only once per crop. Ask the
                    # Server recognizer for a strict four-digit confirmation;
                    # the Server result alone is never enough to promote it.
                    from .paddle_ocr import recognize_line

                    for item in evidence:
                        try:
                            server_rows = recognize_line(
                                item["line_raw"], model_variant="server"
                            )
                        except Exception:
                            server_rows = []
                        strict_server_rows = [
                            row for row in server_rows
                            if row.confidence >= 0.82 and parse_date(row.text) == required
                        ]
                        item["variants"].append({
                            "preprocessing": "日期行 Server 大模型复核",
                            "ocr_texts": [row.text for row in server_rows],
                            "accepted_texts": [row.text for row in strict_server_rows],
                        })
                        if strict_server_rows:
                            item["rows"].extend(strict_server_rows)
                            acceptance_note = "紧裁/宽裁月日一致 + Server 完整日期复核"
                            break
                if acceptance_note:
                    for item in evidence:
                        lx, ly, lw, lh = item["line_box"]
                        x, y, width, height = item["region_box"]
                        accepted_texts = []
                        for row in item["rows"]:
                            if parse_receipt_date(row.text, required) != required:
                                continue
                            accepted_texts.append(row.text)
                            local = TextObservation(
                                text=row.text,
                                confidence=row.confidence,
                                x=lx + row.x * lw,
                                y=ly + row.y * lh,
                                width=row.width * lw,
                                height=row.height * lh,
                            )
                            output.append(TextObservation(
                                text=local.text,
                                confidence=local.confidence,
                                x=x + local.x * width,
                                y=y + local.y * height,
                                width=local.width * width,
                                height=local.height * height,
                            ))
                        for variant in item["variants"]:
                            accepted = [
                                text for text in variant.get("ocr_texts", [])
                                if parse_receipt_date(text, required) == required
                            ]
                            variant["accepted_texts"] = accepted
                            variant["acceptance_note"] = (
                                acceptance_note
                                if accepted else "该预处理未形成可接受日期"
                            )
            # One Mobile line result is never enough to certify a required
            # date.  It can, however, be independently corroborated when the
            # Server recognizer reads the same strict four-digit date from the
            # *other* tight/wide geometry.  Requiring both a different model
            # and a different crop preserves the one-crop 7/9 safety rule.
            if (
                secondary_ocr_backend == "paddle"
                and date_crop_entries
            ):
                required = parse_date(required_text)
                if required is not None:
                    from .paddle_ocr import recognize_line

                    # Do not let a high-confidence *partial* row prevent the
                    # independent audit.  What matters is whether the output
                    # already contains a reliable strict four-digit date, not
                    # which repaired/partial row wins the candidate ranking.
                    needs_confirmation = not any(
                        row.confidence >= 0.72
                        and parse_date(row.text) == required
                        for row in output
                    )
                    confirmed = False
                    mobile_line_evidence = [
                        {
                            "rows": list(crop_entry.get("line_rows") or []),
                            "variants": crop_entry["line_variants"],
                            "line_box": crop_entry["line_box"],
                            "region_box": crop_entry["region_box"],
                            "tight": crop_entry["tight"],
                        }
                        for crop_entry in date_crop_entries
                        if not crop_entry.get("audit_only")
                    ]
                    for mobile_item in mobile_line_evidence if needs_confirmation else []:
                        mobile_rows = [
                            row for row in mobile_item["rows"]
                            if parse_date(row.text) == required
                        ]
                        if not mobile_rows:
                            continue
                        for crop_entry in date_crop_entries:
                            if (
                                crop_entry.get("audit_only")
                                or crop_entry["tight"] == mobile_item["tight"]
                            ):
                                continue
                            try:
                                server_rows = recognize_line(
                                    crop_entry["line_raw"], model_variant="server"
                                )
                            except Exception:
                                server_rows = []
                            strict_server_rows = [
                                row for row in server_rows
                                if row.confidence >= 0.82
                                and parse_date(row.text) == required
                            ]
                            crop_entry["line_variants"].append({
                                "preprocessing": "日期行 Mobile/Server 跨几何复核",
                                "ocr_texts": [row.text for row in server_rows],
                                "accepted_texts": [
                                    row.text for row in strict_server_rows
                                ],
                                "acceptance_note": (
                                    "Mobile 与 Server 在紧/宽不同裁剪上读到同一完整日期"
                                    if strict_server_rows
                                    else "未形成跨模型、跨几何一致日期"
                                ),
                            })
                            if not strict_server_rows:
                                continue
                            for row in mobile_rows:
                                lx, ly, lw, lh = mobile_item["line_box"]
                                x, y, width, height = mobile_item["region_box"]
                                output.append(type(row)(
                                    text=row.text,
                                    confidence=row.confidence,
                                    x=x + (lx + row.x * lw) * width,
                                    y=y + (ly + row.y * lh) * height,
                                    width=row.width * lw * width,
                                    height=row.height * lh * height,
                                ))
                            for row in strict_server_rows:
                                lx, ly, lw, lh = crop_entry["line_box"]
                                x, y, width, height = crop_entry["region_box"]
                                output.append(type(row)(
                                    text=row.text,
                                    confidence=row.confidence,
                                    x=x + (lx + row.x * lw) * width,
                                    y=y + (ly + row.y * lh) * height,
                                    width=row.width * lw * width,
                                    height=row.height * lh * height,
                                ))
                            for variant in mobile_item["variants"]:
                                accepted = [
                                    text for text in variant.get("ocr_texts", [])
                                    if parse_date(text) == required
                                ]
                                if accepted:
                                    variant["accepted_texts"] = accepted
                                    variant["acceptance_note"] = (
                                        "与 Server 另一几何裁剪的完整日期一致"
                                    )
                            confirmed = True
                            break
                        if confirmed:
                            break
            # Colored stamp strokes can obscure an otherwise complete black
            # handwritten date.  The RGB per-pixel maximum suppresses red or
            # blue ink while preserving strokes that are dark in all three
            # channels.  Promote this derivative only with a strict complete
            # date from Mobile and Server on *different* tight/wide crops,
            # confidence >= 0.80 on both, and no other parseable date in the
            # established or new line evidence.  Thus the preprocessing adds
            # evidence but cannot turn one model/crop into a verdict.
            if secondary_ocr_backend == "paddle":
                required = parse_date(required_text)
                needs_max_channel_confirmation = bool(
                    required is not None
                    and not any(
                        row.confidence >= 0.72
                        and parse_date(row.text) == required
                        for row in output
                    )
                )
                if needs_max_channel_confirmation:
                    from .paddle_ocr import recognize_line

                    enhanced_evidence = []
                    enhanced_mismatch_evidence = []
                    enhanced_all_rows = []
                    for crop_entry in date_crop_entries:
                        if (
                            crop_entry.get("audit_only")
                            or crop_entry.get("crop_key") not in {"tight", "wide"}
                        ):
                            continue
                        enhanced_path = crop_entry.get(
                            "line_max_channel_upscaled"
                        )
                        if enhanced_path is None or not enhanced_path.is_file():
                            continue
                        for model_variant, model_label in (
                            ("mobile", "Mobile"),
                            ("server", "Server"),
                        ):
                            try:
                                enhanced_rows = recognize_line(
                                    enhanced_path,
                                    model_variant=model_variant,
                                )
                            except Exception:
                                enhanced_rows = []
                            enhanced_all_rows.extend(enhanced_rows)
                            accepted_pairs = []
                            for row in enhanced_rows:
                                parsed = parse_date(row.text)
                                compact = False
                                # Mobile occasionally preserves all eight
                                # date digits and the terminal ``日`` while
                                # dropping only the printed ``月`` separator
                                # (for example ``2025年1218日``).  This value
                                # is self-contained: no component comes from
                                # ``required``.  It may join the existing
                                # cross-model/cross-geometry route, but only
                                # Server's strict date from the other crop can
                                # confirm it below.
                                if parsed is None and model_variant == "mobile":
                                    parsed = (
                                        _parse_compact_full_date_audit_candidate(
                                            row.text
                                        )
                                    )
                                    compact = parsed is not None
                                if row.confidence < 0.80 or parsed != required:
                                    continue
                                normalized = row
                                if compact:
                                    normalized = type(row)(
                                        text=(
                                            f"{parsed.year}年{parsed.month}月"
                                            f"{parsed.day}日"
                                        ),
                                        confidence=row.confidence,
                                        x=row.x,
                                        y=row.y,
                                        width=row.width,
                                        height=row.height,
                                    )
                                accepted_pairs.append({
                                    "raw_text": row.text,
                                    "row": normalized,
                                    "compact": compact,
                                })
                            strict_rows = [
                                pair["row"] for pair in accepted_pairs
                            ]
                            variant = {
                                "preprocessing": (
                                    "日期行最大通道去彩色三倍放大 "
                                    f"{model_label} 跨几何复核"
                                ),
                                "ocr_texts": [
                                    row.text for row in enhanced_rows
                                ],
                                "accepted_texts": [],
                                "acceptance_note": (
                                    "等待另一模型、另一几何裁剪确认"
                                ),
                            }
                            crop_entry["line_variants"].append(variant)
                            mismatch_by_date: dict[date, list] = {}
                            for row in enhanced_rows:
                                strict_date = parse_date(row.text)
                                if (
                                    row.confidence >= 0.80
                                    and strict_date is not None
                                    and strict_date != required
                                ):
                                    mismatch_by_date.setdefault(
                                        strict_date, []
                                    ).append(row)
                            for mismatch_date, mismatch_rows in (
                                mismatch_by_date.items()
                            ):
                                enhanced_mismatch_evidence.append({
                                    "model": model_variant,
                                    "tight": crop_entry["tight"],
                                    "date": mismatch_date,
                                    "rows": mismatch_rows,
                                    "raw_texts": [
                                        row.text for row in mismatch_rows
                                    ],
                                    "entry": crop_entry,
                                    "variant": variant,
                                })
                            if strict_rows:
                                enhanced_evidence.append({
                                    "model": model_variant,
                                    "tight": crop_entry["tight"],
                                    "rows": strict_rows,
                                    "raw_texts": [
                                        pair["raw_text"]
                                        for pair in accepted_pairs
                                    ],
                                    "compact": any(
                                        pair["compact"]
                                        for pair in accepted_pairs
                                    ),
                                    "entry": crop_entry,
                                    "variant": variant,
                                })
                    established_rows = list(output) + [
                        row
                        for crop_entry in date_crop_entries
                        for row in crop_entry.get("line_rows") or []
                    ]
                    conflicts = _conflicting_receipt_dates(
                        established_rows + enhanced_all_rows, required
                    )
                    confirming_pair = None
                    if not conflicts:
                        for mobile_item in enhanced_evidence:
                            if mobile_item["model"] != "mobile":
                                continue
                            confirming_pair = next((
                                server_item
                                for server_item in enhanced_evidence
                                if server_item["model"] == "server"
                                and server_item["tight"] != mobile_item["tight"]
                            ), None)
                            if confirming_pair is not None:
                                confirming_pair = (mobile_item, confirming_pair)
                                break
                    if confirming_pair is not None:
                        for item in confirming_pair:
                            crop_entry = item["entry"]
                            item["variant"]["accepted_texts"] = item[
                                "raw_texts"
                            ]
                            item["variant"]["acceptance_note"] = (
                                (
                                    "Mobile 自包含8位日期与 Server 严格日期"
                                    "在紧/宽不同裁剪上同日"
                                )
                                if item.get("compact")
                                else (
                                    "Mobile 与 Server 在紧/宽不同裁剪上读到"
                                    "同一完整日期"
                                )
                            )
                            lx, ly, lw, lh = crop_entry["line_box"]
                            x, y, width, height = crop_entry["region_box"]
                            for row in item["rows"]:
                                output.append(type(row)(
                                    text=row.text,
                                    confidence=row.confidence,
                                    x=x + (lx + row.x * lw) * width,
                                    y=y + (ly + row.y * lh) * height,
                                    width=row.width * lw * width,
                                    height=row.height * lh * height,
                                ))
                    else:
                        note = (
                            "存在其他可解析日期，不参与自动判定"
                            if conflicts
                            else "未形成跨模型、跨几何一致日期"
                        )
                        for item in enhanced_evidence:
                            item["variant"]["acceptance_note"] = note
                    mismatch_confirmation = (
                        _cross_model_max_channel_mismatch_date(
                            enhanced_mismatch_evidence,
                            established_rows + enhanced_all_rows,
                            required,
                        )
                    )
                    if mismatch_confirmation is not None:
                        mismatch_date, mismatch_pair = mismatch_confirmation
                        for item in mismatch_pair:
                            crop_entry = item["entry"]
                            item["variant"]["accepted_texts"] = item[
                                "raw_texts"
                            ]
                            item["variant"]["acceptance_note"] = (
                                "Mobile 与 Server 在紧/宽不同最大通道裁剪上"
                                "读到同一完整不匹配日期"
                            )
                            lx, ly, lw, lh = crop_entry["line_box"]
                            x, y, width, height = crop_entry["region_box"]
                            for row in item["rows"]:
                                output.append(type(row)(
                                    text=row.text,
                                    confidence=row.confidence,
                                    x=x + (lx + row.x * lw) * width,
                                    y=y + (ly + row.y * lh) * height,
                                    width=row.width * lw * width,
                                    height=row.height * lh * height,
                                ))
                            artifact_label = (
                                "紧凑区域" if crop_entry["tight"]
                                else "宽区域"
                            )
                            for artifact in artifacts:
                                if artifact.get("variant") == artifact_label:
                                    artifact.update({
                                        "date_max_channel_mismatch_candidate": (
                                            mismatch_date.isoformat()
                                        ),
                                        "date_max_channel_mismatch_note": (
                                            "候选完全来自 Mobile/Server 最大通道"
                                            "日期行，不使用要求到货日期补值"
                                        ),
                                    })
            # A genuine non-matching date is a materially stronger claim than
            # "could not read". In Hybrid mode, promote it only when Mobile's
            # detection pipeline and Server's recognition-only model agree,
            # and the Server supplies a strict four-digit date from the other
            # geometric crop. This recognized 2025-03-06 in a real sample
            # while preserving the review-only outcome for ambiguous 7/9 and
            # 10-like handwriting.
            # A damaged year must not discard an otherwise explicit Mobile
            # month/day.  Admit that weaker Mobile evidence to the mismatch
            # audit only when every valid partial line observation agrees on
            # one non-required date.  It still needs a strict Server date from
            # the *other* geometry below.  This recovers real ``201年4月29日``
            # + ``2025年4月29日`` evidence without promoting samples where the
            # transforms disagree (for example one crop says 1/1, another
            # says 7/1).
            if secondary_ocr_backend == "paddle":
                required = parse_date(required_text)
                partial_line_evidence: list[tuple[dict, date, list]] = []
                partial_dates: set[date] = set()
                if required is not None:
                    for crop_entry in date_crop_entries:
                        if crop_entry.get("audit_only"):
                            continue
                        grouped: dict[date, list] = {}
                        for row in crop_entry.get("line_rows") or []:
                            if row.confidence < 0.72 or parse_date(row.text) is not None:
                                continue
                            month_day = _explicit_trailing_numeric_month_day(
                                row.text, required.year
                            )
                            if month_day is None:
                                continue
                            candidate = date(required.year, *month_day)
                            if candidate == required:
                                continue
                            grouped.setdefault(candidate, []).append(row)
                            partial_dates.add(candidate)
                        for candidate, rows in grouped.items():
                            partial_line_evidence.append(
                                (crop_entry, candidate, rows)
                            )
                if len(partial_dates) == 1:
                    for crop_entry, candidate, rows in partial_line_evidence:
                        if candidate not in partial_dates:
                            continue
                        pending_mismatch_evidence.append({
                            "rows": rows,
                            "variants": crop_entry["line_variants"],
                            "line_box": crop_entry["line_box"],
                            "region_box": crop_entry["region_box"],
                            "tight": crop_entry["tight"],
                            "coordinate_space": "line",
                            "partial_month_day": True,
                        })
            if secondary_ocr_backend == "paddle" and pending_mismatch_evidence:
                required = parse_date(required_text)
                mobile_dates = {
                    parsed
                    for item in pending_mismatch_evidence
                    for row in item["rows"]
                    if (parsed := parse_receipt_date(row.text, required)) is not None
                    and parsed != required
                }
                server_evidence = []
                from .paddle_ocr import recognize_line

                for crop_entry in date_crop_entries:
                    if crop_entry.get("audit_only"):
                        continue
                    tight = crop_entry["tight"]
                    line_raw = crop_entry["line_raw"]
                    line_box = crop_entry["line_box"]
                    region_box = crop_entry["region_box"]
                    line_variants = crop_entry["line_variants"]
                    try:
                        server_rows = recognize_line(line_raw, model_variant="server")
                    except Exception:
                        server_rows = []
                    strict_rows = [
                        row for row in server_rows
                        if row.confidence >= 0.80
                        and (parsed := parse_date(row.text)) in mobile_dates
                        and parsed != required
                    ]
                    line_variants.append({
                        "preprocessing": "日期行 Server 大模型复核不一致日期",
                        "ocr_texts": [row.text for row in server_rows],
                        "accepted_texts": [row.text for row in strict_rows],
                        "acceptance_note": (
                            "Mobile 区域识别 + Server 另一几何裁剪完整日期一致"
                            if strict_rows else "未形成跨模型一致的不匹配日期"
                        ),
                    })
                    for row in strict_rows:
                        server_evidence.append((tight, row, line_box, region_box))
                corroborated = {
                    parse_date(row.text)
                    for tight, row, _, _ in server_evidence
                    if any(
                        not item["tight"] == tight
                        and parse_receipt_date(mobile_row.text, required) == parse_date(row.text)
                        and (
                            row.confidence >= 0.82
                            or (
                                item.get("coordinate_space") == "line"
                                and mobile_row.confidence >= 0.72
                            )
                        )
                        for item in pending_mismatch_evidence
                        for mobile_row in item["rows"]
                    )
                }
                for item in pending_mismatch_evidence:
                    x, y, width, height = item["region_box"]
                    accepted_mobile = []
                    for row in item["rows"]:
                        parsed = parse_receipt_date(row.text, required)
                        if parsed not in corroborated:
                            continue
                        accepted_mobile.append(row.text)
                        if item.get("coordinate_space") == "line":
                            lx, ly, lw, lh = item["line_box"]
                            output.append(type(row)(
                                text=row.text,
                                confidence=row.confidence,
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            ))
                        else:
                            output.append(type(row)(
                                text=row.text,
                                confidence=row.confidence,
                                x=x + row.x * width,
                                y=y + row.y * height,
                                width=row.width * width,
                                height=row.height * height,
                            ))
                    for variant in item.get("variants", []):
                        accepted = [
                            text for text in variant.get("ocr_texts", [])
                            if parse_receipt_date(text, required) in corroborated
                        ]
                        if accepted:
                            variant["accepted_texts"] = accepted
                            variant["acceptance_note"] = "与 Server 另一几何裁剪完整日期一致"
                for tight, row, line_box, region_box in server_evidence:
                    parsed = parse_date(row.text)
                    if parsed not in corroborated:
                        continue
                    lx, ly, lw, lh = line_box
                    x, y, width, height = region_box
                    output.append(type(row)(
                        text=row.text,
                        confidence=row.confidence,
                        x=x + (lx + row.x * lw) * width,
                        y=y + (ly + row.y * lh) * height,
                        width=row.width * lw * width,
                        height=row.height * lh * height,
                    ))
            # If normal Hybrid evidence still produced no date, expose Server
            # recognizer candidates for human review. Partial required-year
            # repairs are capped at 0.45. A literal strict date with a
            # different year is capped at 0.35 and only becomes a displayed
            # audit candidate when the helper below sees it in at least two
            # geometric crops. Neither path can become an automatic verdict.
            if (
                secondary_ocr_backend == "paddle"
                and find_receipt_date(output, required_text)[0] is None
            ):
                required = parse_date(required_text)
                if required is not None:
                    from .paddle_ocr import recognize_line

                    server_strict_audit_texts: list[str] = []
                    for crop_entry in date_crop_entries:
                        if crop_entry.get("audit_only"):
                            continue
                        try:
                            audit_rows = recognize_line(
                                crop_entry["line_raw"], model_variant="server"
                            )
                        except Exception:
                            audit_rows = []
                        partial_rows = [
                            row for row in audit_rows
                            if parse_date(row.text) is None
                            and parse_receipt_date(row.text, required) is not None
                        ]
                        strict_other_year_rows = [
                            row for row in audit_rows
                            if (parsed := parse_date(row.text)) is not None
                            and parsed.year != required.year
                        ]
                        server_strict_audit_texts.extend(
                            row.text for row in audit_rows
                            if parse_date(row.text) is not None
                        )
                        crop_entry["line_variants"].append({
                            "preprocessing": "日期行 Server 大模型低置信度候选",
                            "ocr_texts": [row.text for row in audit_rows],
                            "accepted_texts": [
                                row.text
                                for row in partial_rows + strict_other_year_rows
                            ],
                            "acceptance_note": (
                                "仅作为人工复核候选；跨年份完整日期还需两种几何裁剪一致，"
                                "不参与自动放行"
                            ),
                        })
                        lx, ly, lw, lh = crop_entry["line_box"]
                        x, y, width, height = crop_entry["region_box"]
                        for row in partial_rows:
                            output.append(type(row)(
                                text=row.text,
                                confidence=min(0.45, row.confidence),
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            ))
                        for row in strict_other_year_rows:
                            output.append(type(row)(
                                text=row.text,
                                confidence=min(0.35, row.confidence),
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            ))
                    # A single Server reading is only 44% correct on the
                    # reviewed 301-receipt corpus.  Expose a same-year value
                    # only when Mobile independently reads the exact same
                    # strict date from an Otsu view and all previously parsed
                    # strict line dates agree.  The evidence still comes from
                    # one physical line, so confidence is capped at 45% and
                    # the receipt remains in manual review.
                    if find_receipt_date(output, required_text)[0] is None:
                        existing_strict_texts = [
                            text
                            for crop_entry in date_crop_entries
                            for variant in crop_entry.get("line_variants", [])
                            for text in variant.get("ocr_texts", [])
                        ]
                        for crop_entry in date_crop_entries:
                            if (
                                crop_entry.get("audit_only")
                                or crop_entry.get("crop_key")
                                not in {"tight", "wide"}
                            ):
                                continue
                            otsu_path = crop_entry.get("line_otsu_upscaled")
                            if otsu_path is None or not otsu_path.is_file():
                                continue
                            try:
                                otsu_rows = recognize_line(
                                    otsu_path, model_variant="mobile"
                                )
                            except Exception:
                                otsu_rows = []
                            candidate = _unique_server_mobile_otsu_candidate(
                                server_strict_audit_texts,
                                [row.text for row in otsu_rows],
                                existing_strict_texts,
                            )
                            accepted_otsu = [
                                row for row in otsu_rows
                                if parse_date(row.text) == candidate
                            ] if candidate is not None else []
                            crop_entry["line_variants"].append({
                                "preprocessing": (
                                    "日期行 Otsu 三倍放大 Mobile/Server "
                                    "人工候选"
                                ),
                                "ocr_texts": [row.text for row in otsu_rows],
                                "accepted_texts": [
                                    row.text for row in accepted_otsu
                                ],
                                "acceptance_note": (
                                    "唯一 Server 完整日期与 Mobile Otsu "
                                    "严格同日；同一物理行证据，置信度封顶 45%，"
                                    "仅供人工复核"
                                    if accepted_otsu else
                                    "未形成唯一的跨模型严格同日，不参与判定"
                                ),
                            })
                            if not accepted_otsu:
                                continue
                            row = max(
                                accepted_otsu,
                                key=lambda item: item.confidence,
                            )
                            lx, ly, lw, lh = crop_entry["line_box"]
                            x, y, width, height = crop_entry["region_box"]
                            output.append(type(row)(
                                text=row.text,
                                # Keep the individual observation below the
                                # repeated-audit consensus ceiling as well as
                                # applying the 45% result-level cap above.
                                # This prevents several transforms of the same
                                # physical line from reaching 72% internally.
                                confidence=min(0.25, row.confidence),
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            ))
                            break
                    # A three-times enlarged derivative of the already
                    # line-cleaned image helps the Server recognizer on a few
                    # thin handwritten dates. It remains a single-model audit
                    # source: cap every observation at 0.25 so even matching
                    # tight/wide results cannot reach the reliable threshold.
                    for crop_entry in date_crop_entries:
                        if (
                            crop_entry.get("audit_only")
                            or crop_entry.get("crop_key") not in {"tight", "wide"}
                        ):
                            continue
                        enhanced_path = crop_entry.get(
                            "line_table_clean_upscaled"
                        )
                        if enhanced_path is None or not enhanced_path.is_file():
                            continue
                        try:
                            enhanced_rows = recognize_line(
                                enhanced_path, model_variant="server"
                            )
                        except Exception:
                            enhanced_rows = []
                        accepted_enhanced = [
                            row for row in enhanced_rows
                            if _parse_server_audit_candidate(
                                row.text, required
                            ) is not None
                        ]
                        crop_entry["line_variants"].append({
                            "preprocessing": (
                                "日期行去表格线三倍放大 Server 人工候选"
                            ),
                            "ocr_texts": [row.text for row in enhanced_rows],
                            "accepted_texts": [
                                row.text for row in accepted_enhanced
                            ],
                            "acceptance_note": (
                                "单一大模型放大图证据，仅供人工复核"
                            ),
                        })
                        lx, ly, lw, lh = crop_entry["line_box"]
                        x, y, width, height = crop_entry["region_box"]
                        for row in accepted_enhanced:
                            output.append(type(row)(
                                text=row.text,
                                confidence=min(0.25, row.confidence),
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            ))
                    # OCR sometimes preserves every date digit while dropping
                    # only the printed ``月`` separator, for example
                    # ``2025年1218日``.  A grayscale autocontrast enlargement
                    # exposes that complete eight-digit structure on thin
                    # handwriting.  Accept only a self-contained, valid
                    # YYYYMMDD value ending in ``日``; never repair it from the
                    # required date.  Both Mobile and Server readings remain
                    # low-confidence audit evidence and cannot auto-pass.
                    for crop_entry in date_crop_entries:
                        if (
                            crop_entry.get("audit_only")
                            or crop_entry.get("crop_key") not in {"tight", "wide"}
                        ):
                            continue
                        enhanced_path = crop_entry.get(
                            "line_autocontrast_upscaled"
                        )
                        if enhanced_path is None or not enhanced_path.is_file():
                            continue
                        for model_variant, model_label in (
                            ("mobile", "Mobile"),
                            ("server", "Server"),
                        ):
                            try:
                                enhanced_rows = recognize_line(
                                    enhanced_path, model_variant=model_variant
                                )
                            except Exception:
                                enhanced_rows = []
                            accepted_enhanced = [
                                (row, parsed)
                                for row in enhanced_rows
                                if (
                                    parsed := _parse_compact_full_date_audit_candidate(
                                        row.text
                                    )
                                ) is not None
                            ]
                            crop_entry["line_variants"].append({
                                "preprocessing": (
                                    "日期行灰度自动对比三倍放大 "
                                    f"{model_label} 人工候选"
                                ),
                                "ocr_texts": [row.text for row in enhanced_rows],
                                "accepted_texts": [
                                    row.text for row, _ in accepted_enhanced
                                ],
                                "acceptance_note": (
                                    "完整8位合法日期，仅供人工复核；"
                                    "单一增强路径不自动放行"
                                ),
                            })
                            lx, ly, lw, lh = crop_entry["line_box"]
                            x, y, width, height = crop_entry["region_box"]
                            for row, parsed in accepted_enhanced:
                                output.append(type(row)(
                                    text=(
                                        f"{parsed.year}年{parsed.month}月"
                                        f"{parsed.day}日"
                                    ),
                                    confidence=min(0.25, row.confidence),
                                    x=x + (lx + row.x * lw) * width,
                                    y=y + (ly + row.y * lh) * height,
                                    width=row.width * lw * width,
                                    height=row.height * lh * height,
                                ))
                    # Fixed-template slot audit.  When every established
                    # whole-line path fails, split the printed ``20 年 月 日``
                    # row into a full-year view and a wider month/day view.
                    # The candidate is constructed only from OCR-owned
                    # components: both Mobile and Server line recognizers must
                    # agree on an explicit month/day, and both detector models
                    # must agree on a complete four-digit year.  Even then the
                    # result is normally capped at 25% because every view
                    # comes from one physical row. A later narrow path may
                    # promote a required-date match only when Mobile and
                    # Server recover every component from both approved
                    # color-suppressed variants and no other date exists.
                    current_slot_date, _ = find_receipt_date(
                        output, required_text
                    )
                    required_for_slot = parse_date(required_text)
                    slot_confidence = estimate_date_confidence(
                        output, required_text, current_slot_date
                    )
                    if (
                        required_for_slot is not None
                        and current_slot_date in {None, required_for_slot}
                        and slot_confidence < 0.72
                    ):
                        tight_entry = next(
                            (
                                item for item in date_crop_entries
                                if item.get("crop_key") == "tight"
                                and not item.get("audit_only")
                            ),
                            None,
                        )
                        if tight_entry is not None:
                            try:
                                slot_views = _save_date_slot_views(
                                    tight_entry["line_raw"], temp_dir
                                )
                            except Exception:
                                slot_views = {}
                            year_view = slot_views.get("year_full", {})
                            month_day_view = slot_views.get("month_day", {})
                            year_path = year_view.get("processed")
                            month_day_path = month_day_view.get("processed")
                            slot_variants = []
                            safe_year_variants: list[dict] = []
                            safe_month_day_variants: list[dict] = []
                            month_day_by_model: dict[str, set[tuple[int, int]]] = {}
                            for preprocessing, safe_month_day_path in (
                                ("最大通道去彩色", month_day_path),
                                (
                                    "最大通道去彩色并去横线",
                                    month_day_view.get("line_clean"),
                                ),
                            ):
                                if safe_month_day_path is None:
                                    continue
                                for model_variant, model_label in (
                                    ("mobile", "Mobile"),
                                    ("server", "Server"),
                                ):
                                    try:
                                        slot_rows = recognize_line(
                                            safe_month_day_path,
                                            model_variant=model_variant,
                                        )
                                    except Exception:
                                        slot_rows = []
                                    values = {
                                        parsed for row in slot_rows
                                        if (
                                            parsed := _parse_date_slot_month_day(
                                                row.text
                                            )
                                        ) is not None
                                    }
                                    if preprocessing == "最大通道去彩色":
                                        month_day_by_model[
                                            model_variant
                                        ] = values
                                    safe_variant = {
                                        "slot": "月日联合槽位",
                                        "method": (
                                            f"{model_label} {preprocessing}整行识别"
                                        ),
                                        "model": model_variant,
                                        "preprocessing": preprocessing,
                                        "ocr_texts": [row.text for row in slot_rows],
                                        "parsed_components": [
                                            f"{month:02d}-{day:02d}"
                                            for month, day in sorted(values)
                                        ],
                                    }
                                    slot_variants.append(safe_variant)
                                    safe_month_day_variants.append(safe_variant)
                            for preprocessing, safe_year_path in (
                                ("最大通道去彩色", year_path),
                                (
                                    "最大通道去彩色并去横线",
                                    year_view.get("line_clean"),
                                ),
                            ):
                                if safe_year_path is None:
                                    continue
                                for model_variant, model_label in (
                                    ("mobile", "Mobile"),
                                    ("server", "Server"),
                                ):
                                    try:
                                        safe_year_rows = recognize_line(
                                            safe_year_path,
                                            model_variant=model_variant,
                                        )
                                    except Exception:
                                        safe_year_rows = []
                                    safe_years = {
                                        parsed for row in safe_year_rows
                                        if (
                                            parsed := _parse_date_slot_year(
                                                row.text
                                            )
                                        ) is not None
                                    }
                                    safe_variant = {
                                        "slot": "完整年份槽位",
                                        "method": (
                                            f"{model_label} {preprocessing}整行识别"
                                        ),
                                        "model": model_variant,
                                        "preprocessing": preprocessing,
                                        "ocr_texts": [
                                            row.text for row in safe_year_rows
                                        ],
                                        "parsed_components": [
                                            str(value)
                                            for value in sorted(safe_years)
                                        ],
                                    }
                                    slot_variants.append(safe_variant)
                                    safe_year_variants.append(safe_variant)
                            common_month_days = (
                                month_day_by_model.get("mobile", set())
                                & month_day_by_model.get("server", set())
                            )
                            common_years: set[int] = set()
                            if year_path is not None and len(common_month_days) == 1:
                                from .paddle_ocr import (
                                    recognize_text as paddle_recognize_text,
                                )

                                year_by_model: dict[str, set[int]] = {}
                                for model_variant, model_label in (
                                    ("mobile", "Mobile"),
                                    ("server", "Server"),
                                ):
                                    try:
                                        year_rows = paddle_recognize_text(
                                            year_path,
                                            model_variant=model_variant,
                                            min_text_height=0.001,
                                        )
                                    except Exception:
                                        year_rows = []
                                    years = {
                                        parsed for row in year_rows
                                        if (
                                            parsed := _parse_date_slot_year(
                                                row.text
                                            )
                                        ) is not None
                                    }
                                    year_by_model[model_variant] = years
                                    slot_variants.append({
                                        "slot": "完整年份槽位",
                                        "method": f"{model_label} 检测识别",
                                        "ocr_texts": [row.text for row in year_rows],
                                        "parsed_components": [
                                            str(value) for value in sorted(years)
                                        ],
                                    })
                                common_years = (
                                    year_by_model.get("mobile", set())
                                    & year_by_model.get("server", set())
                                )
                            accepted_slot_date = None
                            slot_candidate_source = ""
                            if len(common_years) == len(common_month_days) == 1:
                                year = next(iter(common_years))
                                month, day = next(iter(common_month_days))
                                try:
                                    accepted_slot_date = date(year, month, day)
                                    slot_candidate_source = (
                                        "Paddle Mobile/Server 年月日槽位一致"
                                    )
                                except ValueError:
                                    accepted_slot_date = None
                            existing_slot_texts = [
                                row.text for row in output
                            ] + [
                                str(text)
                                for entry in date_crop_entries
                                for variant in entry.get("line_variants", [])
                                for text in variant.get("ocr_texts", []) or []
                            ]
                            reliable_slot_date = None
                            if (
                                ocr_backend == "vision"
                                and secondary_ocr_backend == "paddle"
                            ):
                                reliable_slot_date = (
                                    _cross_model_slot_required_date(
                                        safe_year_variants,
                                        safe_month_day_variants,
                                        required_text,
                                        existing_slot_texts,
                                    )
                                )
                            if reliable_slot_date is not None:
                                accepted_slot_date = reliable_slot_date
                                slot_candidate_source = (
                                    "Paddle Mobile/Server 双预处理槽位严格一致"
                                )
                            # Last-resort macOS suggestion for rows where every
                            # established path has *no parseable date evidence*.
                            # Paddle Mobile and Server must agree on an explicit
                            # four-digit year from the standardized white view.
                            # Vision supplies a self-contained month/day from a
                            # separately preprocessed crop-first white view.
                            # This is deliberately one Vision path, so the
                            # resulting observation stays at 22% confidence and
                            # can only populate the pending-review UI.
                            required_for_slot = parse_date(required_text)
                            has_parseable_date_evidence = any(
                                parse_date(row.text) is not None
                                or (
                                    required_for_slot is not None
                                    and parse_receipt_date(
                                        row.text, required_for_slot
                                    ) is not None
                                )
                                for row in output
                            )
                            if (
                                accepted_slot_date is None
                                and ocr_backend == "vision"
                                and secondary_ocr_backend == "paddle"
                                and not has_parseable_date_evidence
                            ):
                                year_white_path = year_view.get(
                                    "white_processed"
                                )
                                vision_month_day_path = month_day_view.get(
                                    "crop_first_white_processed"
                                )
                                fallback_year_by_model: dict[str, set[int]] = {}
                                if year_white_path is not None:
                                    for model_variant, model_label in (
                                        ("mobile", "Mobile"),
                                        ("server", "Server"),
                                    ):
                                        try:
                                            fallback_year_rows = recognize_line(
                                                year_white_path,
                                                model_variant=model_variant,
                                            )
                                        except Exception:
                                            fallback_year_rows = []
                                        fallback_years = {
                                            parsed for row in fallback_year_rows
                                            if (
                                                parsed := _parse_date_slot_year(
                                                    row.text
                                                )
                                            ) is not None
                                        }
                                        fallback_year_by_model[
                                            model_variant
                                        ] = fallback_years
                                        slot_variants.append({
                                            "slot": "完整年份白底槽位",
                                            "method": (
                                                f"{model_label} 整行识别"
                                            ),
                                            "ocr_texts": [
                                                row.text
                                                for row in fallback_year_rows
                                            ],
                                            "parsed_components": [
                                                str(value)
                                                for value in sorted(
                                                    fallback_years
                                                )
                                            ],
                                        })
                                fallback_common_years = (
                                    fallback_year_by_model.get("mobile", set())
                                    & fallback_year_by_model.get("server", set())
                                )
                                vision_month_days: set[tuple[int, int]] = set()
                                if vision_month_day_path is not None:
                                    vision_slot_rows = (
                                        _recognize_date_slot_with_vision(
                                            vision_month_day_path
                                        )
                                    )
                                    vision_month_days = {
                                        parsed for row in vision_slot_rows
                                        if (
                                            parsed := _parse_date_slot_month_day(
                                                row.text
                                            )
                                        ) is not None
                                    }
                                    slot_variants.append({
                                        "slot": "月日裁后去章色白底槽位",
                                        "method": "macOS Vision 精确识别",
                                        "ocr_texts": [
                                            row.text for row in vision_slot_rows
                                        ],
                                        "parsed_components": [
                                            f"{month:02d}-{day:02d}"
                                            for month, day in sorted(
                                                vision_month_days
                                            )
                                        ],
                                    })
                                if (
                                    len(fallback_common_years) == 1
                                    and len(vision_month_days) == 1
                                ):
                                    year = next(iter(fallback_common_years))
                                    month, day = next(iter(vision_month_days))
                                    try:
                                        accepted_slot_date = date(
                                            year, month, day
                                        )
                                        slot_candidate_source = (
                                            "Paddle 双模型年份 + Vision 单路径月日"
                                        )
                                    except ValueError:
                                        accepted_slot_date = None
                            slot_component_candidate = accepted_slot_date
                            if accepted_slot_date is not None:
                                x, y, width, height = tight_entry["region_box"]
                                lx, ly, lw, lh = tight_entry["line_box"]
                                reliable_slot = reliable_slot_date is not None
                                observation_count = 2 if reliable_slot else 1
                                for observation_index in range(
                                    observation_count
                                ):
                                    output.append(TextObservation(
                                        text=(
                                            f"{accepted_slot_date.year}年"
                                            f"{accepted_slot_date.month}月"
                                            f"{accepted_slot_date.day}日"
                                        ),
                                        confidence=(
                                            0.84
                                            if reliable_slot
                                            else 0.22
                                            if "Vision 单路径"
                                            in slot_candidate_source
                                            else 0.25
                                        ),
                                        x=(
                                            x
                                            + (
                                                lx
                                                + (0.30 + 0.01 * observation_index)
                                                * lw
                                            )
                                            * width
                                        ),
                                        y=y + ly * height,
                                        width=0.68 * lw * width,
                                        height=lh * height,
                                    ))
                            tight_artifact = next(
                                (
                                    item for item in artifacts
                                    if item.get("variant") == "紧凑区域"
                                ),
                                None,
                            )
                            if tight_artifact is not None and slot_views:
                                prefix = artifact_url_prefix.rstrip("/")
                                tight_artifact.update({
                                    "date_slot_year_original_url": (
                                        f"{prefix}/date/"
                                        f"{year_view['original'].name}"
                                    ),
                                    "date_slot_year_processed_url": (
                                        f"{prefix}/date/"
                                        f"{year_view['processed'].name}"
                                    ),
                                    "date_slot_year_line_clean_url": (
                                        f"{prefix}/date/"
                                        f"{year_view['line_clean'].name}"
                                    ),
                                    "date_slot_month_day_original_url": (
                                        f"{prefix}/date/"
                                        f"{month_day_view['original'].name}"
                                    ),
                                    "date_slot_month_day_processed_url": (
                                        f"{prefix}/date/"
                                        f"{month_day_view['processed'].name}"
                                    ),
                                    "date_slot_month_day_line_clean_url": (
                                        f"{prefix}/date/"
                                        f"{month_day_view['line_clean'].name}"
                                    ),
                                    "date_slot_year_white_url": (
                                        f"{prefix}/date/"
                                        f"{year_view['white_processed'].name}"
                                    ),
                                    "date_slot_month_day_vision_url": (
                                        f"{prefix}/date/"
                                        f"{month_day_view['crop_first_white_processed'].name}"
                                    ),
                                    "date_slot_ocr_variants": slot_variants,
                                    "date_slot_candidate": (
                                        slot_component_candidate.isoformat()
                                        if slot_component_candidate else ""
                                    ),
                                    "date_slot_reliable": bool(
                                        reliable_slot_date
                                    ),
                                    "date_slot_acceptance_note": (
                                        (
                                            "Mobile/Server 在去彩色及去横线槽位"
                                            "独立组成要求日期，且无其他日期冲突；"
                                            "可自动核验"
                                            if reliable_slot_date is not None
                                            else
                                            "Paddle Mobile/Server 完整年份一致，"
                                            "Vision 单路径月日建议；低置信度标黄，"
                                            "必须人工复核"
                                            if "Vision 单路径" in slot_candidate_source
                                            else
                                            "年份检测与月日整行识别均经 Mobile/Server 一致；"
                                            "同一物理日期行的槽位组合仅供人工复核"
                                        )
                                        if slot_component_candidate
                                        else "槽位组件未形成跨模型完整一致日期"
                                    ),
                                    "date_slot_candidate_source": (
                                        slot_candidate_source
                                        if slot_component_candidate else ""
                                    ),
                                })
            # A business-impossible candidate (for example a date earlier
            # than document creation) must not prevent one final audit of the
            # untouched color handwriting.  Run this only when every existing
            # parseable date is impossible by that independent business rule,
            # or when there is no parseable date at all.  Three Vision language
            # configurations must agree on one complete date from the same
            # original-color white canvas.  This remains 20% review evidence;
            # it never resolves a plausible conflict or becomes reliable.
            if ocr_backend == "vision" and secondary_ocr_backend == "paddle":
                required_for_white = parse_date(required_text)
                creation_for_white = parse_date(creation_text)
                existing_dates = {
                    parsed
                    for row in output
                    if (
                        parsed := (
                            parse_date(row.text)
                            or (
                                parse_receipt_date(
                                    row.text, required_for_white
                                )
                                if required_for_white is not None
                                else None
                            )
                        )
                    ) is not None
                }
                viable_existing_dates = {
                    value for value in existing_dates
                    if creation_for_white is None
                    or value >= creation_for_white
                }
                if not viable_existing_dates:
                    tight_entry = next(
                        (
                            item for item in date_crop_entries
                            if item.get("crop_key") == "tight"
                            and not item.get("audit_only")
                        ),
                        None,
                    )
                    tight_artifact = next(
                        (
                            item for item in artifacts
                            if item.get("variant") == "紧凑区域"
                        ),
                        None,
                    )
                    line_white_path = (
                        tight_entry.get("line_white_standardized")
                        if tight_entry is not None else None
                    )
                    if (
                        tight_entry is not None
                        and tight_artifact is not None
                        and line_white_path is not None
                        and line_white_path.is_file()
                    ):
                        (
                            line_white_variants,
                            line_white_dates,
                        ) = _recognize_date_line_vision_consensus(
                            line_white_path
                        )
                        tight_entry["line_variants"].extend(
                            line_white_variants
                        )
                        if len(line_white_dates) == 1:
                            line_white_candidate = next(
                                iter(line_white_dates)
                            )
                            if (
                                creation_for_white is not None
                                and line_white_candidate < creation_for_white
                            ):
                                tight_artifact.update({
                                    "date_line_white_rejected_candidate": (
                                        line_white_candidate.isoformat()
                                    ),
                                    "date_line_white_acceptance_note": (
                                        "Vision 白底多配置候选早于制单日期，"
                                        "仅保留原始证据，未用于日期判定"
                                    ),
                                })
                            else:
                                x, y, width, height = tight_entry[
                                    "region_box"
                                ]
                                lx, ly, lw, lh = tight_entry["line_box"]
                                output.append(TextObservation(
                                    text=(
                                        f"{line_white_candidate.year}年"
                                        f"{line_white_candidate.month}月"
                                        f"{line_white_candidate.day}日"
                                    ),
                                    confidence=0.20,
                                    x=x + lx * width,
                                    y=y + ly * height,
                                    width=lw * width,
                                    height=lh * height,
                                ))
                                tight_artifact.update({
                                    "date_line_white_candidate": (
                                        line_white_candidate.isoformat()
                                    ),
                                    "date_line_white_acceptance_note": (
                                        "原色白底日期行在三种 Vision 语言"
                                        "配置下输出同一完整日期；单一系统"
                                        "模型证据仅供人工复核，置信度封顶 20%"
                                    ),
                                })
            return output, artifacts


def _find_low_confidence_date_audit(
    rows: list[TextObservation],
) -> tuple[date | None, TextObservation | None]:
    """Expose repeated strict audit dates without making them reliable.

    ``find_receipt_date`` intentionally accepts only the normal footer band.
    A far-below handwritten note therefore needs a separate path.  Require two
    strict observations of the same date and the audit confidence cap; this
    returns a display/review candidate, never an automatic decision.
    """
    grouped: dict[date, list[TextObservation]] = {}
    for row in rows:
        if float(row.confidence) > 0.35:
            continue
        parsed = parse_date(row.text)
        if parsed is not None:
            grouped.setdefault(parsed, []).append(row)
    supported = [
        (parsed, evidence) for parsed, evidence in grouped.items()
        if len(evidence) >= 2
    ]
    if not supported:
        return None, None
    parsed, evidence = max(
        supported,
        key=lambda item: (len(item[1]), max(row.confidence for row in item[1])),
    )
    return parsed, max(evidence, key=lambda row: row.confidence)


def _find_confirmed_far_lower_date(
    rows: list[TextObservation],
    artifacts: list[dict],
) -> tuple[date | None, TextObservation | None]:
    """Select only a far-below date carrying the strict cross-model marker."""
    candidates = {
        parsed
        for artifact in artifacts
        if (parsed := parse_date(
            str(artifact.get("far_lower_cross_model_candidate", ""))
        )) is not None
    }
    if len(candidates) != 1:
        return None, None
    candidate = next(iter(candidates))
    evidence = [
        row for row in rows
        if parse_date(row.text) == candidate and row.confidence > 0.35
    ]
    if len(evidence) < 2:
        return None, None
    return candidate, max(evidence, key=lambda row: row.confidence)


def _trailing_numeric_month_day(text: str, required) -> bool:
    """Return whether OCR safely exposes the required trailing ``M.D``.

    A four-digit year, when present, remains authoritative and must agree.
    Short/noisy leading digits are deliberately ignored only here, where a
    second OCR model must independently produce the same month/day before the
    analyzer constructs a normalized date observation.
    """
    month_day = _explicit_trailing_numeric_month_day(text, required.year)
    return month_day == (required.month, required.day)


def _conflicting_receipt_dates(rows: list[TextObservation], required) -> set[date]:
    """Return parseable local candidates that contradict the target date."""
    return {
        parsed
        for row in rows
        if (parsed := parse_receipt_date(row.text, required)) is not None
        and parsed != required
    }


def _parse_server_audit_candidate(text: str, required):
    """Parse a low-confidence Server candidate without changing its month.

    The general receipt parser may inherit the required month when OCR only
    exposes a day. That is useful for established cross-model paths, but a
    single-model enlarged audit image must not turn literal ``1月5日`` or
    invalid ``0月9日`` into the required August date.
    """
    strict = parse_date(text)
    if strict is not None:
        return strict
    repaired = parse_receipt_date(text, required)
    if repaired is None:
        return None
    compact = re.sub(r"\s+", "", text)
    month_token = re.search(r"(\d{1,4})月", compact)
    if month_token:
        explicit_month = int(month_token.group(1))
        if not 1 <= explicit_month <= 12:
            return None
        if repaired.month != explicit_month:
            return None
    return repaired


def _unique_server_mobile_otsu_candidate(
    server_texts: list[str],
    mobile_otsu_texts: list[str],
    existing_strict_texts: list[str],
) -> date | None:
    """Return one strict cross-model date for manual review only.

    The Server candidate must be unique, Mobile must independently produce
    the same complete date on the Otsu image, and no already parsed strict
    date may contradict it.  This helper deliberately does not use the
    required-delivery date as a repair source.
    """
    server_dates = {
        parsed for text in server_texts
        if (parsed := parse_date(text)) is not None
    }
    if len(server_dates) != 1:
        return None
    candidate = next(iter(server_dates))
    mobile_dates = {
        parsed for text in mobile_otsu_texts
        if (parsed := parse_date(text)) is not None
    }
    if mobile_dates != {candidate}:
        return None
    existing_dates = {
        parsed for text in existing_strict_texts
        if (parsed := parse_date(text)) is not None
    }
    if existing_dates and existing_dates != {candidate}:
        return None
    return candidate


def _repeated_strict_date_across_variants(
    variants: list[dict],
) -> date | None:
    """Return one strict date observed in at least two preprocessing views."""
    support: dict[date, set[str]] = {}
    for variant in variants:
        label = str(variant.get("preprocessing", ""))
        for text in variant.get("ocr_texts", []) or []:
            parsed = parse_date(str(text))
            if parsed is not None:
                support.setdefault(parsed, set()).add(label)
    repeated = [value for value, labels in support.items() if len(labels) >= 2]
    return repeated[0] if len(repeated) == 1 else None


def _cross_model_far_lower_strict_date(
    mobile_variants: list[dict],
    server_variants: list[dict],
    existing_texts: list[str],
) -> date | None:
    """Confirm one far-below strict date across models and crop geometries.

    Mobile reads the complete audit region while Server reads a narrower date
    line. Each model must repeat the same literal four-digit date in two
    preprocessing variants. Any other literal strict date vetoes promotion;
    the printed required date is never used for parsing or repair.
    """
    mobile = _repeated_strict_date_across_variants(mobile_variants)
    server = _repeated_strict_date_across_variants(server_variants)
    if mobile is None or server != mobile:
        return None
    existing = {
        parsed
        for text in existing_texts
        if (parsed := parse_date(str(text))) is not None
    }
    return mobile if not (existing - {mobile}) else None


def _cross_model_max_channel_mismatch_date(
    evidence: list[dict],
    all_rows: list[TextObservation],
    required: date,
) -> tuple[date, tuple[dict, dict]] | None:
    """Confirm one strict non-required date across models and geometries.

    Every component must be present in literal OCR text from the maximum-
    channel date-line view.  Mobile and Server must agree while using opposite
    tight/wide crops, and any other receipt-date interpretation vetoes the
    route.  The required-delivery date is comparison-only and never repairs a
    candidate.
    """
    pairs: dict[date, tuple[dict, dict]] = {}
    for mobile_item in evidence:
        candidate = mobile_item.get("date")
        if (
            mobile_item.get("model") != "mobile"
            or not isinstance(candidate, date)
            or candidate == required
        ):
            continue
        server_item = next((
            item for item in evidence
            if item.get("model") == "server"
            and item.get("date") == candidate
            and item.get("tight") != mobile_item.get("tight")
        ), None)
        if server_item is not None:
            pairs[candidate] = (mobile_item, server_item)
    if len(pairs) != 1:
        return None
    candidate, pair = next(iter(pairs.items()))
    observed = {
        parsed
        for row in all_rows
        if (parsed := parse_receipt_date(row.text, required)) is not None
    }
    if observed - {candidate}:
        return None
    return candidate, pair


def _parse_compact_full_date_audit_candidate(text: str) -> date | None:
    """Parse a complete YYYYMMDD audit reading with a missing separator.

    The terminal ``日`` and all eight digits must be present in the OCR text.
    No component is inherited from the required date, keeping this narrower
    than the ordinary receipt-date repair path.
    """
    compact = re.sub(
        r"\s+", "", text.replace("O", "0").replace("o", "0")
    )
    match = re.search(
        r"(?<!\d)(20\d{2})(?:年)?(\d{2})(\d{2})日(?!\d)", compact
    )
    if not match:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None




def _explicit_trailing_numeric_month_day(
    text: str, expected_year: int,
) -> tuple[int, int] | None:
    """Extract an explicit trailing month/day while treating year carefully."""
    compact = re.sub(r"\s", "", text.replace("O", "0").replace("o", "0"))
    explicit_year = re.search(r"(?<!\d)(\d{4})(?!\d)", compact)
    if explicit_year and int(explicit_year.group(1)) != expected_year:
        return None
    match = re.search(r"(\d{1,2})(?:[./-]|月)(\d{1,2})日?\D*$", compact)
    if not match:
        return None
    try:
        month, day = int(match.group(1)), int(match.group(2))
        date(expected_year, month, day)
    except ValueError:
        return None
    return month, day


def _save_date_line_crop(
    source: str | Path,
    destination: str | Path,
    *,
    tight: bool,
    lower: bool = False,
) -> tuple[float, float, float, float]:
    """Save the handwritten date row inside a date-region crop."""
    # The tight crop still contains the cell above the handwritten date.  Start
    # below that border so recognition-only sees one line, not two touching
    # rows; the wide crop needs a slightly earlier start for tilted scans.
    # The below-table variant has no table row above it. Preserve the full
    # glyph height and remove only the signature/mark area on its far left.
    if lower:
        left, top = 0.34, 0.0
    else:
        left, top = ((0.22, 0.36) if tight else (0.28, 0.42))
    right, bottom = 0.995, 0.995
    with Image.open(source) as image:
        width, height = image.size
        cropped = image.crop((
            round(left * width), round(top * height),
            round(right * width), round(bottom * height),
        ))
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.lower() in {".jpg", ".jpeg"}:
            cropped.convert("RGB").save(destination, quality=95)
        else:
            cropped.save(destination)
    return left, top, right - left, bottom - top


def _save_date_slot_views(
    source: str | Path,
    destination_dir: str | Path,
) -> dict[str, dict[str, Path]]:
    """Save auditable year and month/day views from the fixed date template.

    The Samsung form prints ``20 年 月 日`` around handwriting. Whole-line OCR
    often merges those glyphs with a stamp or the table border. These two
    overlapping views preserve the printed units while giving the recognizer
    enough horizontal context to avoid clipping a leading day digit.
    """
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    slots = {
        "year_full": (0.0, 0.52),
        "month_day": (0.30, 0.985),
    }
    output: dict[str, dict[str, Path]] = {}
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
        red, green, blue = rgb.split()
        max_channel = ImageChops.lighter(red, ImageChops.lighter(green, blue))
        max_channel = ImageOps.autocontrast(max_channel, cutoff=1)
        for name, (left, right) in slots.items():
            box = (
                round(rgb.width * left), 0,
                round(rgb.width * right), rgb.height,
            )
            raw_crop = rgb.crop(box)
            clean_crop = max_channel.crop(box)
            crop_red, crop_green, crop_blue = raw_crop.split()
            crop_first_clean = ImageOps.autocontrast(
                ImageChops.lighter(
                    crop_red, ImageChops.lighter(crop_green, crop_blue)
                ),
                cutoff=1,
            )
            raw_path = destination / f"date-slot-{name}-original.jpg"
            clean_path = destination / f"date-slot-{name}-max-channel.png"
            white_path = (
                destination / f"date-slot-{name}-max-channel-white.png"
            )
            crop_first_white_path = (
                destination
                / f"date-slot-{name}-crop-first-max-channel-white.png"
            )
            line_clean_path = (
                destination
                / f"date-slot-{name}-max-channel-line-clean.png"
            )
            raw_crop.save(raw_path, quality=95)
            clean_crop.resize(
                (
                    max(180, clean_crop.width * 3),
                    max(96, clean_crop.height * 3),
                ),
                Image.Resampling.LANCZOS,
            ).save(clean_path)
            try:
                import cv2
                import numpy as np

                clean_array = np.asarray(clean_crop)
                ink = cv2.threshold(
                    clean_array,
                    0,
                    255,
                    cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
                )[1]
                horizontal = cv2.morphologyEx(
                    ink,
                    cv2.MORPH_OPEN,
                    cv2.getStructuringElement(
                        cv2.MORPH_RECT,
                        (max(18, ink.shape[1] // 3), 2),
                    ),
                )
                line_clean = Image.fromarray(
                    cv2.bitwise_not(cv2.subtract(ink, horizontal))
                )
                line_clean.resize(
                    (
                        max(180, line_clean.width * 3),
                        max(96, line_clean.height * 3),
                    ),
                    Image.Resampling.LANCZOS,
                ).save(line_clean_path)
            except Exception:
                clean_crop.resize(
                    (
                        max(180, clean_crop.width * 3),
                        max(96, clean_crop.height * 3),
                    ),
                    Image.Resampling.LANCZOS,
                ).save(line_clean_path)
            _date_slot_white_canvas(clean_crop).save(white_path)
            _date_slot_white_canvas(crop_first_clean).save(
                crop_first_white_path
            )
            output[name] = {
                "original": raw_path,
                "processed": clean_path,
                "line_clean": line_clean_path,
                "white_processed": white_path,
                "crop_first_white_processed": crop_first_white_path,
            }
    return output


def _date_slot_white_canvas(image: Image.Image) -> Image.Image:
    """Standardize a small handwritten slot for OCR and visual review."""
    target_height = 180
    width = max(
        1, round(image.width * target_height / max(1, image.height))
    )
    normalized = image.resize(
        (width, target_height), Image.Resampling.LANCZOS
    ).convert("RGB")
    canvas = Image.new("RGB", (max(720, width + 160), 320), "white")
    canvas.paste(
        normalized,
        ((canvas.width - width) // 2, (canvas.height - target_height) // 2),
    )
    return canvas.resize(
        (canvas.width * 3, canvas.height * 3), Image.Resampling.LANCZOS
    )


def _recognize_date_slot_with_vision(path: str | Path) -> list[TextObservation]:
    """Run the optional macOS recognizer without breaking Windows imports."""
    try:
        from .vision_ocr import recognize_text as vision_recognize_text

        return vision_recognize_text(
            path,
            languages=("zh-Hans", "en-US"),
            min_text_height=0.001,
            fast=False,
            custom_words=(),
            language_correction=False,
        )
    except Exception:
        return []


def _recognize_date_line_vision_consensus(
    path: str | Path,
) -> tuple[list[dict], set[date]]:
    """Return complete dates stable across three Vision language settings.

    The configurations share one physical image and one system model, so the
    result is review-only evidence.  Requiring all three nevertheless rejects
    configuration-sensitive hallucinations while preserving the original
    color handwriting; destructive max-channel preprocessing is intentionally
    excluded after it dropped the tens digit in a real ``24`` regression.
    """
    try:
        from .vision_ocr import recognize_text as vision_recognize_text
    except Exception:
        return [], set()
    configurations = (
        ("中英关闭纠错", ("zh-Hans", "en-US"), False),
        ("中英开启纠错", ("zh-Hans", "en-US"), True),
        ("仅中文关闭纠错", ("zh-Hans",), False),
    )
    variants = []
    date_sets: list[set[date]] = []
    for label, languages, language_correction in configurations:
        try:
            rows = vision_recognize_text(
                path,
                languages=languages,
                min_text_height=0.001,
                fast=False,
                custom_words=(),
                language_correction=language_correction,
            )
        except Exception:
            rows = []
        values = {
            parsed for row in rows
            if (parsed := parse_date(row.text)) is not None
        }
        date_sets.append(values)
        variants.append({
            "preprocessing": (
                f"原日期行白底标准化 Vision {label} 人工候选"
            ),
            "ocr_texts": [row.text for row in rows],
            "accepted_texts": [],
            "acceptance_note": (
                "三种 Vision 语言配置必须输出同一完整日期；"
                "同一系统模型证据仅供人工复核"
            ),
        })
    common = set.intersection(*date_sets) if date_sets else set()
    if len(common) == 1:
        accepted = next(iter(common))
        for variant in variants:
            variant["accepted_texts"] = [
                text for text in variant["ocr_texts"]
                if parse_date(text) == accepted
            ]
    return variants, common


def _parse_date_slot_year(text: str) -> int | None:
    """Return only a complete OCR-owned four-digit year before ``年``."""
    compact = re.sub(r"\s+", "", text.replace("O", "0").replace("o", "0"))
    match = re.search(r"(?<!\d)(20\d{2})年", compact)
    return int(match.group(1)) if match else None


def _parse_date_slot_month_day(text: str) -> tuple[int, int] | None:
    """Return an explicit month/day pair; never inherit either component."""
    compact = re.sub(r"\s+", "", text.replace("O", "0").replace("o", "0"))
    match = re.search(r"(\d{1,2})(?:[./-]|月)(\d{1,2})(?:日)?", compact)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    try:
        date(2000, month, day)
    except ValueError:
        return None
    return month, day


def _cross_model_slot_required_date(
    year_variants: list[dict],
    month_day_variants: list[dict],
    required_text: str,
    existing_texts: list[str],
) -> date | None:
    """Promote only a fully OCR-owned slot date matching the requirement.

    Mobile and Server must each expose exactly the same complete four-digit
    year and explicit month/day across the approved color-suppressed views.
    The printed required date is only the final comparison target; it never
    fills a missing component. Any other parseable date on the physical line
    vetoes promotion, so recognized mismatches remain in manual review.
    """
    required = parse_date(required_text)
    if required is None:
        return None

    years = {"mobile": set(), "server": set()}
    month_days = {"mobile": set(), "server": set()}
    for variant in year_variants:
        model = str(variant.get("model", ""))
        if model not in years:
            continue
        for text in variant.get("ocr_texts", []) or []:
            parsed = _parse_date_slot_year(str(text))
            if parsed is not None:
                years[model].add(parsed)
    for variant in month_day_variants:
        model = str(variant.get("model", ""))
        if model not in month_days:
            continue
        for text in variant.get("ocr_texts", []) or []:
            parsed = _parse_date_slot_month_day(str(text))
            if parsed is not None:
                month_days[model].add(parsed)

    expected_year = {required.year}
    expected_month_day = {(required.month, required.day)}
    if not all(
        years[model] == expected_year
        and month_days[model] == expected_month_day
        for model in ("mobile", "server")
    ):
        return None

    existing_dates: set[date] = set()
    for text in existing_texts:
        parsed = parse_date(str(text)) or parse_receipt_date(
            str(text), required
        )
        if parsed is not None:
            existing_dates.add(parsed)
    if existing_dates - {required}:
        return None
    return required


def _save_date_upper_line_crop(
    source: str | Path,
    destination: str | Path,
) -> tuple[float, float, float, float]:
    """Save a right-aligned handwritten date placed in the row above."""
    left, top, right, bottom = 0.46, 0.0, 0.995, 0.58
    with Image.open(source) as image:
        width, height = image.size
        cropped = image.crop((
            round(left * width), round(top * height),
            round(right * width), round(bottom * height),
        ))
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.lower() in {".jpg", ".jpeg"}:
            cropped.convert("RGB").save(destination, quality=95)
        else:
            cropped.save(destination)
    return left, top, right - left, bottom - top


def _recover_signature_requirement(
    source: str | Path,
    page_rows: list[TextObservation],
    primary: str,
    page_backend: str,
    customer: str = "",
) -> dict | None:
    """Retry the fixed signature-requirement row without text detection."""
    if page_backend not in {"paddle", "paddle_server"}:
        return None
    anchor = next(
        (
            row for row in page_rows
            if any(token in row.text for token in ("签章要求", "签幸要求", "签草要求"))
        ),
        None,
    )
    anchor_y = anchor.y if anchor else 0.468
    with Image.open(source) as image, tempfile.TemporaryDirectory(prefix="signature-line-") as temp_dir:
        width, height = image.size
        y1 = max(0, round((anchor_y - 0.004) * height))
        y2 = min(height, round((anchor_y + 0.025) * height))
        crop = image.crop((round(0.015 * width), y1, round(0.64 * width), y2))
        path = Path(temp_dir) / "signature-requirement.jpg"
        crop.convert("RGB").save(path, quality=95)
        try:
            from .paddle_ocr import recognize_line

            model_variant = "server" if page_backend == "paddle_server" else "mobile"
            line_rows = recognize_line(path, model_variant=model_variant)
        except Exception:
            return None
    if not line_rows:
        return None
    best = max(line_rows, key=lambda row: row.confidence)
    raw = best.text.strip()
    match = re.search(r"签[章幸草]要求\s*[:：]?\s*(.+)$", raw)
    candidate = match.group(1).strip() if match else ""
    candidate = _trim_signature_requirement_candidate(candidate)
    if not candidate or len(normalize_text(candidate)) < 6:
        return None
    primary_key = normalize_text(primary)
    candidate_key = normalize_text(candidate)
    if len(primary_key) >= 6:
        # A primary value ending in a known numbered service-center structure
        # is already complete business evidence. Recognition-only retries can
        # easily append characters from the neighboring quantity cells (for
        # example ``筑业业兴``) while remaining superficially similar. Do not
        # replace the structured primary merely because the noisy retry is
        # longer.
        if re.search(
            r"(?:维修中心|售后)\d{6,10}(?:站)?$", primary_key
        ):
            return None
        # The full-page detector can preserve the stable Samsung
        # ``...服务中`` grammar while losing only the final ``心``.  The
        # contextual repair below has an intentionally narrow rule for that
        # one-glyph loss.  A wider recognition-only crop sometimes drops an
        # earlier glyph and appends fragments of the neighbouring fixed note
        # (for example ``三电子孝感服务中单整收``); being longer must not make
        # that candidate preferable to the structurally safer primary value.
        if re.fullmatch(
            r"三星电子[\u4e00-\u9fff（）()]{2,16}服务中", primary_key
        ):
            return None
        from difflib import SequenceMatcher

        similarity = SequenceMatcher(None, primary_key, candidate_key).ratio()
        if similarity < 0.72 or len(candidate_key) <= len(primary_key):
            return None
        customer_key = normalize_text(customer)
        if customer_key:
            primary_customer = SequenceMatcher(None, primary_key, customer_key).ratio()
            candidate_customer = SequenceMatcher(None, candidate_key, customer_key).ratio()
            # When the requirement is recognizably the customer entity, a
            # retry must not replace it with a longer but less accurate line.
            if max(primary_customer, candidate_customer) >= 0.55 and candidate_customer < primary_customer:
                return None
    return {
        "value": candidate,
        "confidence": round(float(best.confidence), 3),
        "raw": raw,
    }


def _trim_signature_requirement_candidate(value: str) -> str:
    """Remove right-hand quantity cells accidentally joined to the field."""
    candidate = re.split(r"实?收数(?:量)?|拒收数(?:量)?|签收说明", value, maxsplit=1)[0]
    candidate = re.sub(r"(?:整|数)?签收$", "", candidate)
    # In the fixed receipt layout the next right-hand heading is ``实收数量``.
    # A narrow OCR row can retain only its first glyph and append it to an
    # otherwise complete service-centre requirement.  Limit this cleanup to
    # the exact business suffix so genuine names ending in “收” are not
    # trimmed generically.
    candidate = re.sub(r"(服务中心)收$", r"\1", candidate)
    # A contact-name requirement may end in a mobile number.  The following
    # fixed note begins with “视为整单…”, and OCR can append only “为整” to
    # that number.  Require the full phone-shaped suffix before removing it;
    # arbitrary organization text ending in the same characters is retained.
    candidate = re.sub(r"(\d{7,11})为整(?:单)?(?:完)?$", r"\1", candidate)
    phone = re.search(r"电话\s*[:：]?\s*[0-9\-]{7,}", candidate)
    if phone:
        candidate = candidate[: phone.end()]
    elif re.search(r"(?:收货章|专用章)", candidate):
        # Some legitimate requirement identifiers follow the stamp type:
        # ``检测专用章（3）`` and ``维修专用章045153608531``.  The old boundary
        # cleanup stopped exactly at ``专用章`` and silently discarded these
        # business-significant suffixes.  Preserve only a tightly structured
        # parenthesized index and/or 6-12 digit identifier; arbitrary adjacent
        # quantity-cell noise is still cut away.
        end = max(
            match.end()
            for match in re.finditer(
                r"(?:收货章|专用章)"
                r"(?:\s*[（(]\d{1,3}[）)])?"
                r"(?:\s*\d{6,12}(?:站)?)?",
                candidate,
            )
        )
        candidate = candidate[:end]
    else:
        numbered_center = re.search(r"维修中心\s*\d{6,10}", candidate)
        if numbered_center:
            candidate = candidate[: numbered_center.end()]
        else:
            # One reviewed pickup requirement continues after the station id
            # with an independently printed 11-digit phone number and legal
            # company name. Preserve that complete grammar before applying
            # the ordinary ``站代码 + id`` boundary.
            station_contact = re.search(
                r"站代码\s*[:：]?\s*\d{6,10}\s*"
                r"\d{10,12}\s*[\u4e00-\u9fff]{4,40}有限公司",
                candidate,
            )
            if station_contact:
                candidate = candidate[: station_contact.end()]
            else:
                authorization_code = re.search(
                    r"(?:授权代码|站代码)\s*[:：]?\s*\d{6,10}",
                    candidate,
                )
                if authorization_code:
                    candidate = candidate[: authorization_code.end()]
    return candidate.rstrip("：:；;，,。 ")


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = "".join(cleaned.split())
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def _recover_missing_product_grades(
    source: Path,
    page_rows: list[TextObservation],
    product_table: dict,
    ocr_backend: str,
) -> dict:
    """Retry a narrow enlarged table crop when the one-letter grade vanished."""
    missing_indexes = [
        index for index, row in enumerate(product_table.get("rows", []))
        if (
            not str(row.get("values", {}).get("等级", "")).strip()
            or not re.fullmatch(r"[A-D]", str(row.get("values", {}).get("等级", "")).strip())
            or float(row.get("confidences", {}).get("等级", 0)) < LOW_CONFIDENCE_THRESHOLD
        )
    ]
    if not missing_indexes:
        return product_table
    header = next(
        (row for row in page_rows if "行号" in row.text and 0.30 <= row.y <= 0.55),
        None,
    )
    if header is None:
        return product_table
    signature = next((row for row in page_rows if "签章要求" in row.text and row.y > header.y), None)
    left, right = 0.39, 0.58
    top = max(0.0, header.y - 0.018)
    row_numbers = {
        normalize_text(str(row.get("values", {}).get("行号", "")))
        for row in product_table.get("rows", [])
    }
    row_anchors = [
        row for row in page_rows
        if row.x < 0.115 and normalize_text(row.text) in row_numbers and row.y > header.y
    ]
    table_bottom = max(
        (row.y + row.height + 0.006 for row in row_anchors),
        default=header.y + 0.10,
    )
    bottom_limit = signature.y - 0.008 if signature else 0.95
    bottom = min(max(table_bottom, min(header.y + 0.10, bottom_limit)), bottom_limit)
    if bottom <= top + 0.025:
        return product_table

    with Image.open(source) as image, tempfile.TemporaryDirectory(prefix="receipt-grade-") as temp_dir:
        width, height = image.size
        crop = image.crop((int(left * width), int(top * height), int(right * width), int(bottom * height)))
        crop = crop.resize((crop.width * 5, crop.height * 5), Image.Resampling.BICUBIC)
        crop_path = Path(temp_dir) / "grade-crop.png"
        crop.save(crop_path)
        try:
            crop_rows = recognize_text(crop_path, backend=ocr_backend)
        except Exception:
            crop_rows = []

        # Detection may merge a nearby material suffix and the one-letter
        # grade. Retry each missing cell using its row-number Y coordinate and
        # Paddle's recognition-only model on the exact grade column.
        from .paddle_ocr import recognize_line

        line_model = "server" if ocr_backend == "paddle_server" else "mobile"
        recovered_by_index: dict[int, tuple[str, float]] = {}
        for index in missing_indexes:
            detail = product_table["rows"][index]
            row_number = normalize_text(str(detail.get("values", {}).get("行号", "")))
            row_anchor = next(
                (
                    row for row in page_rows
                    if row.x < 0.115
                    and normalize_text(row.text) == row_number
                    and header.y < row.y < bottom
                ),
                None,
            )
            if row_anchor is None:
                continue
            grade_left, grade_right = 0.43, 0.49
            grade_top = max(top, row_anchor.y + 0.001)
            grade_bottom = min(bottom, row_anchor.y + row_anchor.height + 0.003)
            grade_crop = image.crop((
                int(grade_left * width), int(grade_top * height),
                int(grade_right * width), int(grade_bottom * height),
            ))
            grade_crop = grade_crop.resize(
                (max(1, grade_crop.width * 5), max(1, grade_crop.height * 5)),
                Image.Resampling.BICUBIC,
            )
            grade_path = Path(temp_dir) / f"grade-row-{index}.png"
            grade_crop.save(grade_path)
            try:
                line_rows = recognize_line(grade_path, model_variant=line_model)
            except Exception:
                line_rows = []
            for row in line_rows:
                cleaned = re.sub(r"[^A-Z]", "", row.text.upper())
                if re.fullmatch(r"[A-D]", cleaned) and row.confidence >= 0.60:
                    recovered_by_index[index] = (cleaned, float(row.confidence))
                    crop_rows.append(TextObservation(
                        cleaned, row.confidence,
                        (0.43 - left) / (right - left),
                        (grade_top - top) / (bottom - top),
                        (0.49 - 0.43) / (right - left),
                        max(0.001, (grade_bottom - grade_top) / (bottom - top)),
                    ))
                    break

    recovered: list[TextObservation] = []
    for row in crop_rows:
        cleaned = re.sub(r"[^A-Z0-9]", "", row.text.upper())
        global_x = left + row.x * (right - left)
        global_width = row.width * (right - left)
        center_x = global_x + global_width / 2
        if not re.fullmatch(r"[A-Z]", cleaned) or not 0.43 <= center_x < 0.49:
            continue
        recovered.append(TextObservation(
            cleaned,
            row.confidence,
            global_x,
            top + row.y * (bottom - top),
            global_width,
            row.height * (bottom - top),
        ))
    if not recovered:
        return product_table

    retried = parse_product_table(page_rows + recovered)
    if len(retried.get("rows", [])) != len(product_table.get("rows", [])):
        return product_table
    for index in missing_indexes:
        old = product_table["rows"][index]
        new = retried["rows"][index]
        old_grade = str(old.get("values", {}).get("等级", "")).strip()
        direct_grade = recovered_by_index.get(index)
        grade = (
            direct_grade[0]
            if direct_grade else str(new.get("values", {}).get("等级", "")).strip()
        )
        if not re.fullmatch(r"[A-D]", grade):
            continue
        old["values"]["等级"] = grade
        material = str(old.get("values", {}).get("物料编号", ""))
        if re.search(r"[\u4e00-\u9fff]" + re.escape(grade) + r"$", material):
            old["values"]["物料编号"] = material[: -len(grade)]
            old.setdefault("sources", {})["物料编号"] = "OCR + 独立等级单元格拆分"
        old.setdefault("original_values", {}).setdefault("等级", old_grade)
        old.setdefault("confidences", {})["等级"] = (
            direct_grade[1]
            if direct_grade else new.get("confidences", {}).get("等级", 0.0)
        )
        old.setdefault("sources", {})["等级"] = f"{ocr_backend} 商品等级局部放大 OCR"
        old["low_confidence_columns"] = [
            name for name in old.get("low_confidence_columns", []) if name != "等级"
        ]
    return product_table


def _prefer_detail_requirement(primary: str, detail: str) -> bool:
    """Select a fuller secondary requirement without accepting unrelated text."""
    from difflib import SequenceMatcher

    primary_key = "".join(str(primary).split())
    detail_key = "".join(str(detail).split())
    if len(detail_key) < 6:
        return False
    if len(primary_key) < 6:
        return True
    # ``维修专章`` is itself a complete printed stamp type on reviewed
    # Samsung receipts. Vision may hallucinate the conventional extra ``用``
    # and look one glyph "fuller" than Paddle's exact reading. Keep the
    # primary form value in this one unambiguous insertion case; seal matching
    # handles ``维修专章``/``维修专用章`` as equivalent separately.
    if (
        primary_key.endswith("维修专章")
        and detail_key == primary_key.removesuffix("维修专章") + "维修专用章"
    ):
        return False
    similarity = SequenceMatcher(None, primary_key, detail_key).ratio()
    return similarity >= 0.72 and len(detail_key) >= len(primary_key) + 1


def _prefer_detail_field(primary: str, detail: str) -> bool:
    """Accept a fuller secondary business field only when both readings agree."""
    from difflib import SequenceMatcher

    primary_key = "".join(str(primary).split())
    detail_key = "".join(str(detail).split())
    if len(primary_key) < 6 or len(detail_key) <= len(primary_key):
        return False
    if len(detail_key) > len(primary_key) + 6:
        return False
    return SequenceMatcher(None, primary_key, detail_key).ratio() >= 0.82


def _fuse_product_material(primary: str, detail: str) -> str:
    """Keep Paddle's model code while taking a fuller Vision CJK description."""
    pattern = re.compile(
        r"^(?P<code>[A-Z0-9/\-]+)(?P<description>[\u4e00-\u9fff]+)"
        r"\s*(?P<capacity>\d+(?:G|TB))$",
        re.IGNORECASE,
    )
    primary_match = pattern.fullmatch("".join(str(primary).split()))
    detail_match = pattern.fullmatch("".join(str(detail).split()))
    if not primary_match or not detail_match:
        return ""
    if primary_match.group("capacity").upper() != detail_match.group("capacity").upper():
        return ""
    primary_description = primary_match.group("description")
    detail_description = detail_match.group("description")
    if len(detail_description) <= len(primary_description) or len(detail_description) > len(primary_description) + 2:
        return ""
    if primary_description not in detail_description:
        return ""
    return (
        primary_match.group("code").upper()
        + detail_description
        + primary_match.group("capacity").upper()
    )


def _fuse_product_descriptions(primary_table: dict, detail_table: dict) -> None:
    """Fuse same-EAN product descriptions and retain both engines' evidence."""
    detail_by_ean = {
        str(row.get("values", {}).get("EAN码", "")).replace(" ", ""): row
        for row in detail_table.get("rows", [])
        if row.get("values", {}).get("EAN码")
    }
    changed = False
    for row in primary_table.get("rows", []):
        values = row.get("values", {})
        ean = str(values.get("EAN码", "")).replace(" ", "")
        detail_row = detail_by_ean.get(ean)
        if not detail_row:
            continue
        original = str(values.get("物料编号", ""))
        detail_value = str(detail_row.get("values", {}).get("物料编号", ""))
        fused = _fuse_product_material(original, detail_value)
        if not fused or fused == original:
            continue
        row.setdefault("original_values", {}).setdefault("物料编号", original)
        row["original_values"]["物料编号_Vision"] = detail_value
        values["物料编号"] = fused
        primary_confidence = float(row.get("confidences", {}).get("物料编号", 0))
        detail_confidence = float(detail_row.get("confidences", {}).get("物料编号", 0))
        confidence = round(min(primary_confidence, detail_confidence), 3)
        row.setdefault("confidences", {})["物料编号"] = confidence
        row.setdefault("sources", {})["物料编号"] = "Paddle编码 + Vision商品描述补全"
        low_columns = set(row.get("low_confidence_columns", []))
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            low_columns.add("物料编号")
        row["low_confidence_columns"] = [
            name for name in row.get("values", {}) if name in low_columns
        ]
        required_scores = [
            float(row.get("confidences", {}).get(name, 0))
            for name in ("行号", "产品类别", "物料编号", "出库仓库", "数量", "重量", "体积", "EAN码")
        ]
        row["row_confidence"] = round(sum(required_scores) / len(required_scores), 3)
        changed = True
    if changed:
        scores = [
            float(row.get("confidences", {}).get(name, 0))
            for row in primary_table.get("rows", [])
            for name in ("行号", "产品类别", "物料编号", "出库仓库", "数量", "重量", "体积", "EAN码")
        ]
        primary_table["confidence"] = round(sum(scores) / len(scores), 3)
        primary_table["source"] += " + 同EAN跨引擎描述补全"


def decide_overall(date_check: dict, seal_check: dict, review_reasons: list[str]) -> str:
    """Never auto-pass or auto-fail when date/seal evidence itself is unreliable."""
    if review_reasons or not date_check.get("reliable") or not seal_check.get("reliable"):
        return "需人工复核"
    if date_check.get("status") == "不匹配" or seal_check.get("status") == "不匹配":
        return "不通过"
    if date_check.get("status") == "匹配" and seal_check.get("status") == "匹配":
        return "通过"
    return "需人工复核"


def _collect_business_rejected_date_evidence(
    rows: list[TextObservation],
    required_text: str,
    creation_text: str,
    tracking_text: str = "",
) -> list[dict]:
    """Preserve every OCR date rejected by the outbound-date lower bound."""
    required = parse_date(required_text)
    grouped: dict[date, set[str]] = {}
    lower_bound = None
    for row in rows:
        parsed = parse_date(row.text)
        if parsed is None and required is not None:
            parsed = parse_receipt_date(row.text, required)
        if parsed is None:
            continue
        _, rejected, candidate_lower_bound = _reject_date_before_creation(
            parsed, creation_text, tracking_text
        )
        lower_bound = candidate_lower_bound or lower_bound
        if rejected is not None:
            grouped.setdefault(rejected, set()).add(str(row.text))
    return [
        {
            "value": value.isoformat(),
            "ocr_texts": sorted(texts),
            "reason": (
                f"早于最早出库业务日期 {lower_bound.isoformat()}"
                if lower_bound else "早于最早出库业务日期"
            ),
        }
        for value, texts in sorted(grouped.items())
    ]


def _reject_date_before_creation(
    actual_date, creation_text: str, tracking_text: str = ""
):
    """Reject dates before the earliest documented outbound business date.

    A receipt can be reprinted after delivery, so the visible creation date is
    not always the original outbound date. Samsung waybill numbers embed the
    dispatch date (``WYYYYMMDD-...``); when both are present, use the earlier
    one as the lower bound while leaving the printed field value unchanged.
    """
    creation_date = parse_date(creation_text)
    tracking_match = re.search(r"W(20\d{2})(\d{2})(\d{2})", tracking_text)
    tracking_date = parse_date(
        "-".join(tracking_match.groups()) if tracking_match else ""
    )
    business_dates = [value for value in (creation_date, tracking_date) if value]
    lower_bound = min(business_dates) if business_dates else None
    if actual_date and lower_bound and actual_date < lower_bound:
        return None, actual_date, lower_bound
    return actual_date, None, lower_bound


def combine_region_texts(values: list[str]) -> str:
    """Fuse OCR fragments only inside one detected seal region."""
    return "".join(_dedupe(values))


def _extract_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        output: list[str] = []
        for item in value.values():
            output.extend(_extract_strings(item))
        return output
    if isinstance(value, list):
        output = []
        for item in value:
            output.extend(_extract_strings(item))
        return output
    return []
