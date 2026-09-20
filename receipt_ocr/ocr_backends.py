from __future__ import annotations

import importlib.util
import os
from importlib.metadata import version as _distribution_version
from pathlib import Path

from .ocr_types import TextObservation, observations_text


BACKEND_LABELS = {
    "danzhengtong": "单证通",
    "paddle": "PaddleOCR PP-OCRv5 Mobile",
    "paddle_server": "PaddleOCR PP-OCRv5 Server（大模型）",
    "paddle_v6": "PaddleOCR PP-OCRv6 Small",
    "paddle_seal": "PaddleOCR 印章专用检测模型",
    "hybrid_server": "混合 OCR（Paddle Server 页面 + Paddle Mobile 日期/印章）",
}

# The macOS Vision backend was removed.  Selected ids may still sit in saved
# tasks and result rows, so they stay resolvable and displayable instead of
# turning an old record into a hard error: a legacy id maps onto the local
# Paddle route that replaced it, and keeps its own label for display.
LEGACY_BACKEND_ALIASES = {
    "vision": "paddle",
    # "hybrid" used to mean Paddle Mobile page + Vision details.  With Vision
    # gone it degenerates to a single Paddle Mobile route.
    "hybrid": "paddle",
}
LEGACY_BACKEND_LABELS = {
    "vision": "macOS Vision（已下线）",
    "hybrid": "混合 OCR（Paddle Mobile + Vision，已下线）",
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
    from .paddle_ocr import paddle_model_home, server_max_side

    single_model_safety = "单一 Paddle 模型：日期同日可匹配，印章候选进入人工复核"
    paddle_v6_ready = paddle_installed and paddle_v6_supported()
    return [
        {
            "id": "paddle",
            "label": BACKEND_LABELS["paddle"],
            "available": paddle_installed,
            "reason": "" if paddle_installed else "需安装 paddlepaddle 与 paddleocr",
            "model_home": str(paddle_model_home()),
            "model_name": "PP-OCRv5 mobile",
            "safety_policy": single_model_safety,
            "recommended": True,
            "usage_note": "默认引擎；速度和字段/商品准确率较均衡",
        },
        {
            "id": "paddle_server",
            "label": BACKEND_LABELS["paddle_server"],
            "available": paddle_installed,
            "reason": "" if paddle_installed else "需安装 paddlepaddle 与 paddleocr",
            "model_home": str(paddle_model_home()),
            "model_name": "PP-OCRv5 server",
            "max_page_side": server_max_side(),
            "safety_policy": single_model_safety,
            "recommended": False,
            "usage_note": "大模型对照或局部重试；整页更耗内存，当前不建议整批默认",
        },
        {
            "id": "paddle_v6",
            "label": BACKEND_LABELS["paddle_v6"],
            "available": paddle_v6_ready,
            "reason": "" if paddle_v6_ready else "需 paddleocr>=3.7.0（PP-OCRv6 模型）",
            "model_home": str(paddle_model_home()),
            "model_name": "PP-OCRv6 small",
            "safety_policy": single_model_safety,
            "recommended": False,
            "usage_note": "新一代多语种模型对照；整页与日期需与 PP-OCRv5 结果交叉核对",
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
        {
            "id": "hybrid_server",
            "label": BACKEND_LABELS["hybrid_server"],
            "available": paddle_installed,
            "reason": "" if paddle_installed else "需安装 paddlepaddle 与 paddleocr",
            "model_home": str(paddle_model_home()),
            "model_name": "页面：PP-OCRv5 server；日期/印章：PP-OCRv5 mobile",
            "max_page_side": server_max_side(),
            "safety_policy": "Paddle Server 表单 + Paddle Mobile 日期/印章：跨模型证据仍按严格规则核验",
            "recommended": False,
            "usage_note": "密集表格对照或指定批次重试；大模型结果仍需与 Mobile/真值核对",
            "route": {
                "page": BACKEND_LABELS["paddle_server"],
                "date": BACKEND_LABELS["paddle"],
                "seal": BACKEND_LABELS["paddle"],
            },
        },
    ]


def default_backend() -> str:
    configured = os.getenv("OCR_BACKEND", "").strip().lower()
    if configured and configured != "auto":
        return resolve_backend(configured)
    available = {item["id"] for item in backend_catalog() if item["available"]}
    if "paddle" in available:
        return "paddle"
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
    if selected != "hybrid_server":
        return {stage: selected for stage in OCR_STAGES}
    return {"page": "paddle_server", "date": "paddle", "seal": "paddle"}


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
    "recognize_text", "paddle_v6_supported",
]
