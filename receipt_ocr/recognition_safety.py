"""Shared limits on evidence from a single physical OCR model."""

from __future__ import annotations


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
    physical = {
        str(stage_backends.get(stage) or "") for stage in ("page", "date", "seal")
    }
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
