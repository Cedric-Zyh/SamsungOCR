"""Shared limits on evidence from a single physical OCR model."""

from __future__ import annotations


def _apply_single_paddle_safety(
    date_check: dict,
    seal_check: dict,
    stage_backends: dict[str, str],
) -> str:
    """Prevent a single Paddle model from certifying its own transforms.

    Every local route now runs one Paddle tier across the page, date and seal
    stages. Raw/clean/line variants remain useful OCR evidence, but they are
    observations by the same model and must not be mistaken for independent
    corroboration. A genuinely cross-model ``hybrid_server`` route (Server page
    + Mobile details) is not affected. Exact date and strict seal-text matches
    use the normal business comparison rule without requiring another model.
    """
    physical = {
        str(stage_backends.get(stage) or "") for stage in ("page", "date", "seal")
    }
    from .paddle_ocr import is_paddle_backend
    if len(physical) != 1 or not all(is_paddle_backend(name) for name in physical):
        return ""
    policy = "单一 Paddle 模型安全模式：日期同日匹配、印章文字严格完整匹配可直接通过，其他结果需人工复核"
    date_is_exact_match = (
        date_check.get("status") == "匹配"
        and bool(date_check.get("required"))
        and bool(date_check.get("actual"))
        and date_check.get("required") == date_check.get("actual")
    )
    date_check["confidence"] = round(
        min(0.68, float(date_check.get("confidence") or 0)), 3
    )
    date_check["reliable"] = date_is_exact_match
    date_check["safety_policy"] = policy
    seal_check["confidence"] = round(
        min(0.68, float(seal_check.get("confidence") or 0)), 3
    )
    # 印章沿用严格三态比对：忽略空格及中英文括号后，完整一致就是
    # “匹配”。这种结果不需要再被单模型安全规则降级；安全规则仍然
    # 约束部分匹配、漏字、错字和未识别结果。
    from .parsing_seals import normalize_seal_text_strict

    expected_seal = normalize_seal_text_strict(seal_check.get("requirement") or "")
    actual_seal = normalize_seal_text_strict(seal_check.get("recognized") or "")
    seal_is_strict_exact_match = (
        seal_check.get("comparison_policy") == "strict_text"
        and seal_check.get("status") == "匹配"
        and bool(expected_seal)
        and expected_seal == actual_seal
        and not seal_check.get("simulated")
    )
    seal_check["reliable"] = seal_is_strict_exact_match
    seal_check["safety_policy"] = policy
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
