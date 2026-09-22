from __future__ import annotations

import importlib.util
import os
from importlib.metadata import version as _distribution_version
from pathlib import Path

from .ocr_types import TextObservation, observations_text


BACKEND_LABELS = {
    "danzhengtong": "单证通",
    "paddle_v6": "PaddleOCR PP-OCRv6 Small",
    "paddle_seal": "PaddleOCR 印章专用检测模型",
}

# The macOS Vision backend was removed.  Selected ids may still sit in saved
# tasks and result rows, so they stay resolvable and displayable instead of
# turning an old record into a hard error: a legacy id maps onto the local
# Paddle route that replaced it, and keeps its own label for display.
LEGACY_BACKEND_ALIASES = {
    # Older saved tasks may still contain these ids.  They are deliberately
    # redirected to the remaining local model so removed v5 models can never
    # be loaded again.
    "paddle": "paddle_v6",
    "paddle_server": "paddle_v6",
    "hybrid_server": "paddle_v6",
    "vision": "paddle_v6",
    "hybrid": "paddle_v6",
}
LEGACY_BACKEND_LABELS = {
    "paddle": "Paddle Mobile（已下线，已转 PP-OCRv6 Small）",
    "paddle_server": "Paddle Server（已下线，已转 PP-OCRv6 Small）",
    "hybrid_server": "混合 OCR（已下线，已转 PP-OCRv6 Small）",
    "vision": "macOS Vision（已下线，已转 PP-OCRv6 Small）",
    "hybrid": "混合 OCR（已下线，已转 PP-OCRv6 Small）",
}

OCR_STAGES = ("page", "date", "seal")

# PP-OCRv6 model names only exist from PaddleOCR 3.7.0 on. Advertising the
# backend on an older install would offer an entry that fails at load time.
PADDLE_V6_MIN_VERSION = (3, 7)


def _paddleocr_version() -> tuple[int, ...]:
    try:
        numbers = []
        for part in _distribution_version("paddleocr").split("."):
            digits = "".join(ch for ch in part if ch.isdigit())
            if not part[:1].isdigit() or not digits:
                break
            numbers.append(int(digits))
        return tuple(numbers)
    except Exception:
        return ()


def paddle_v6_supported() -> bool:
    return _paddleocr_version() >= PADDLE_V6_MIN_VERSION



def backend_catalog() -> list[dict]:
    paddle_installed = (
        importlib.util.find_spec("paddle") is not None
        and importlib.util.find_spec("paddleocr") is not None
    )
    from .paddle_ocr import paddle_model_home

    single_model_safety = "单一 Paddle 模型：日期同日可匹配，印章候选进入人工复核"
    paddle_v6_ready = paddle_installed and paddle_v6_supported()
    return [
        {
            "id": "paddle_v6",
            "label": BACKEND_LABELS["paddle_v6"],
            "available": paddle_v6_ready,
            "reason": "" if paddle_v6_ready else "需 paddleocr>=3.7.0（PP-OCRv6 模型）",
            "model_home": str(paddle_model_home()),
            "model_name": "PP-OCRv6 small",
            "safety_policy": single_model_safety,
            "recommended": True,
            "usage_note": "当前默认本地模型；整页、日期和印章使用同一 v6 路线",
        },
        {
            "id": "paddle_seal",
            "label": BACKEND_LABELS["paddle_seal"],
            "available": paddle_v6_ready,
            "reason": "" if paddle_v6_ready else "需 paddleocr>=3.7.0（印章检测模型）",
            "model_home": str(paddle_model_home()),
            "model_name": "PP-OCRv4_mobile_seal_det + PP-OCRv6_small_rec",
            "safety_policy": "仅读取印章区域三张处理图，不参与页面、日期或商品识别",
            "recommended": False,
            "usage_note": "印章专用检测；首次使用需下载 PP-OCRv4_mobile_seal_det 权重",
        },
    ]


def default_backend() -> str:
    configured = os.getenv("OCR_BACKEND", "").strip().lower()
    if configured and configured != "auto":
        return resolve_backend(configured)
    available = {item["id"] for item in backend_catalog() if item["available"]}
    if "paddle_v6" in available:
        return "paddle_v6"
    raise RuntimeError("没有可用的 OCR 引擎")


def resolve_backend(name: str | None) -> str:
    requested = (name or "auto").strip().lower()
    if requested == "auto":
        return default_backend()
    requested = LEGACY_BACKEND_ALIASES.get(requested, requested)
    item = next((entry for entry in backend_catalog() if entry["id"] == requested), None)
    if item is None:
        raise ValueError(f"未知 OCR 引擎: {requested}")
    if not item["available"]:
        raise RuntimeError(f"OCR 引擎 {item['label']} 不可用：{item['reason']}")
    return requested


def normalize_backend_id(name: str | None) -> str:
    """Map ids from old saved plans without bringing retired models back."""
    requested = (name or "").strip().lower()
    return LEGACY_BACKEND_ALIASES.get(requested, requested)


def backend_label(name: str) -> str:
    return BACKEND_LABELS.get(name) or LEGACY_BACKEND_LABELS.get(name, name)


def backend_route(name: str | None) -> dict[str, str]:
    """Resolve the physical OCR backend used by each analysis stage."""
    selected = resolve_backend(name)
    if selected == "paddle_seal":
        # This backend is intentionally valid only for the seal stage.  The
        # page/date route remains a normal local model so the seal-only model
        # can never become a document OCR provider by accident.
        return {"page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_seal"}
    return {stage: selected for stage in OCR_STAGES}


def backend_route_labels(name: str | None) -> dict[str, str]:
    return {stage: backend_label(value) for stage, value in backend_route(name).items()}


def recognize_text(
    image_path: str | Path,
    *,
    backend: str | None = None,
    stage: str = "page",
    **kwargs,
) -> list[TextObservation]:
    if stage not in OCR_STAGES:
        raise ValueError(f"未知 OCR 阶段: {stage}")
    if str(backend or "").strip().lower() == "paddle_seal":
        # The stamp-only backend has its own detector+recognizer entry point;
        # never silently downgrade a generic call to page OCR.
        return []
    selected = backend_route(backend)[stage]
    from .recognition_scope import provider_allowed
    if not provider_allowed(selected):
        return []
    from .paddle_ocr import is_paddle_backend, variant_of
    if is_paddle_backend(selected):
        from .paddle_ocr import recognize_text as recognize
        kwargs["model_variant"] = variant_of(selected)
    else:  # pragma: no cover - resolve_backend prevents this.
        raise ValueError(selected)
    return recognize(image_path, **kwargs)


__all__ = [
    "TextObservation", "observations_text", "backend_catalog", "backend_label",
    "backend_route", "backend_route_labels", "default_backend", "resolve_backend",
    "recognize_text", "paddle_v6_supported", "normalize_backend_id",
]
