from __future__ import annotations

import importlib.util
import os
import platform
from pathlib import Path

from .ocr_types import TextObservation, observations_text


BACKEND_LABELS = {
    "vision": "macOS Vision",
    "paddle": "PaddleOCR PP-OCRv5 Mobile",
    "paddle_server": "PaddleOCR PP-OCRv5 Server（大模型）",
    "hybrid": "混合 OCR（Paddle Mobile + Vision）",
    "hybrid_server": "混合 OCR（Paddle Server + Vision）",
}

OCR_STAGES = ("page", "date", "seal")


def backend_catalog() -> list[dict]:
    is_macos = platform.system() == "Darwin"
    vision_installed = is_macos and importlib.util.find_spec("Vision") is not None
    paddle_installed = (
        importlib.util.find_spec("paddle") is not None
        and importlib.util.find_spec("paddleocr") is not None
    )
    from .paddle_ocr import paddle_model_home, server_max_side

    local_detail_backend = "vision" if vision_installed else "paddle"
    local_detail_label = BACKEND_LABELS[local_detail_backend]
    single_model_safety = "单一 Paddle 模型：日期/印章候选自动进入人工复核"
    return [
        {
            "id": "vision",
            "label": BACKEND_LABELS["vision"],
            "available": vision_installed,
            "reason": "" if vision_installed else "仅 macOS 且需安装 pyobjc Vision",
            "model_home": "系统内置",
            "recommended": False,
            "usage_note": "适合 macOS 快速对照；商品表格不如 Paddle 按列识别稳定",
        },
        {
            "id": "paddle",
            "label": BACKEND_LABELS["paddle"],
            "available": paddle_installed,
            "reason": "" if paddle_installed else "需安装 paddlepaddle 与 paddleocr",
            "model_home": str(paddle_model_home()),
            "model_name": "PP-OCRv5 mobile",
            "safety_policy": single_model_safety,
            "recommended": not is_macos,
            "usage_note": "Windows 推荐默认；速度和字段/商品准确率较均衡",
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
            "id": "hybrid",
            "label": BACKEND_LABELS["hybrid"],
            "available": paddle_installed,
            "reason": "" if paddle_installed else "需安装 paddlepaddle 与 paddleocr",
            "model_home": str(paddle_model_home()),
            "model_name": "表单/商品：PP-OCRv5 mobile",
            "safety_policy": "" if vision_installed else single_model_safety,
            "recommended": bool(is_macos and vision_installed),
            "usage_note": (
                "macOS 推荐默认：Paddle 识别表单/商品，Vision 识别日期/印章"
                if vision_installed else
                "当前无 Vision，三个阶段均回退 Paddle Mobile"
            ),
            "route": {
                "page": BACKEND_LABELS["paddle"],
                "date": local_detail_label,
                "seal": local_detail_label,
            },
        },
        {
            "id": "hybrid_server",
            "label": BACKEND_LABELS["hybrid_server"],
            "available": paddle_installed,
            "reason": "" if paddle_installed else "需安装 paddlepaddle 与 paddleocr",
            "model_home": str(paddle_model_home()),
            "model_name": "表单/商品：PP-OCRv5 server",
            "max_page_side": server_max_side(),
            "safety_policy": (
                "" if vision_installed
                else "Paddle Server 表单 + Paddle Mobile 日期/印章：跨模型证据仍按严格规则核验"
            ),
            "recommended": False,
            "usage_note": "密集表格对照或指定批次重试；大模型结果仍需与 Mobile/真值核对",
            "route": {
                "page": BACKEND_LABELS["paddle_server"],
                "date": local_detail_label,
                "seal": local_detail_label,
            },
        },
    ]


def default_backend() -> str:
    configured = os.getenv("OCR_BACKEND", "").strip().lower()
    if configured:
        return resolve_backend(configured)
    available = {item["id"] for item in backend_catalog() if item["available"]}
    if platform.system() == "Darwin" and "hybrid" in available and "vision" in available:
        return "hybrid"
    if platform.system() == "Darwin" and "vision" in available:
        return "vision"
    if "paddle" in available:
        return "paddle"
    raise RuntimeError("没有可用的 OCR 引擎")


def resolve_backend(name: str | None) -> str:
    requested = (name or "auto").strip().lower()
    if requested == "auto":
        return default_backend()
    item = next((entry for entry in backend_catalog() if entry["id"] == requested), None)
    if item is None:
        raise ValueError(f"未知 OCR 引擎: {requested}")
    if not item["available"]:
        raise RuntimeError(f"OCR 引擎 {item['label']} 不可用：{item['reason']}")
    return requested


def backend_label(name: str) -> str:
    return BACKEND_LABELS.get(name, name)


def backend_route(name: str | None) -> dict[str, str]:
    """Resolve the physical OCR backend used by each analysis stage."""
    selected = resolve_backend(name)
    if selected not in {"hybrid", "hybrid_server"}:
        return {stage: selected for stage in OCR_STAGES}
    page_backend = "paddle_server" if selected == "hybrid_server" else "paddle"
    available = {item["id"] for item in backend_catalog() if item["available"]}
    detail_backend = "vision" if "vision" in available else "paddle"
    return {"page": page_backend, "date": detail_backend, "seal": detail_backend}


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
    selected = backend_route(backend)[stage]
    if selected == "vision":
        from .vision_ocr import recognize_text as recognize
    elif selected in {"paddle", "paddle_server"}:
        from .paddle_ocr import recognize_text as recognize
        kwargs["model_variant"] = "server" if selected == "paddle_server" else "mobile"
    else:  # pragma: no cover - resolve_backend prevents this.
        raise ValueError(selected)
    return recognize(image_path, **kwargs)


__all__ = [
    "TextObservation", "observations_text", "backend_catalog", "backend_label",
    "backend_route", "backend_route_labels", "default_backend", "resolve_backend",
    "recognize_text",
]
