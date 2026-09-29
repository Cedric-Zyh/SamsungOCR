from __future__ import annotations

import importlib.util
import os
from importlib.metadata import version as _distribution_version
from pathlib import Path

from ..domain.ocr import TextObservation, observations_text
from .registry import PROVIDERS
from .paddle import PaddleTextProvider


# Public mappings retained for existing integrations; definitions live in the registry.
BACKEND_LABELS = {item.id: item.label for item in PROVIDERS if item.id != "qingtong"}
LEGACY_BACKEND_ALIASES = dict(PROVIDERS.aliases)
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
    from .paddle_runtime import paddle_model_home

    ready = paddle_installed and paddle_v6_supported()
    model_home = str(paddle_model_home())
    return [
        {
            "id": item.id,
            "label": item.label,
            "available": ready,
            "reason": "" if ready else item.unavailable_reason,
            "model_home": model_home,
            "model_name": item.model_name,
            "safety_policy": item.safety_policy,
            "recommended": item.recommended,
            "usage_note": item.usage_note,
        }
        for item in PROVIDERS
        if item.kind in {"local_text", "local_seal"}
    ]


def default_backend() -> str:
    configured = os.getenv("OCR_BACKEND", "").strip().lower()
    if configured and configured != "auto":
        return resolve_backend(configured)
    available = {item["id"] for item in backend_catalog() if item["available"]}
    for definition in PROVIDERS:
        if definition.recommended and definition.id in available:
            return definition.id
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
    return PROVIDERS.require(selected).route()


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
    from ..runtime.scope import provider_allowed
    if not provider_allowed(selected):
        return []
    return PaddleTextProvider(selected).recognize(image_path, **kwargs)


__all__ = [
    "TextObservation", "observations_text", "backend_catalog", "backend_label",
    "backend_route", "backend_route_labels", "default_backend", "resolve_backend",
    "recognize_text", "paddle_v6_supported", "normalize_backend_id",
]
