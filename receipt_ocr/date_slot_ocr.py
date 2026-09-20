"""Collect optional Paddle evidence from date lines and slots."""

from __future__ import annotations
from pathlib import Path
from .date_fragments import (
    _parse_adaptive_day_slot,
    _parse_date_slot_digit,
)


def _recognize_day_slot_variants(day_view: dict[str, Path]) -> list[dict]:
    """Recognize the fixed day-only crop with both Paddle model sizes."""
    try:
        from .paddle_ocr import recognize_line
    except Exception:
        return []
    variants = []
    for preprocessing, path in (
        ("最大通道去彩色", day_view.get("processed")),
        ("最大通道去彩色并去横线", day_view.get("line_clean")),
    ):
        if path is None:
            continue
        for model, label in (("mobile", "Mobile"), ("server", "Server")):
            try:
                rows = recognize_line(path, model_variant=model)
            except Exception:
                rows = []
            values = {
                parsed
                for row in rows
                if (parsed := _parse_date_slot_digit(row.text, maximum=31)) is not None
            }
            variants.append(
                {
                    "slot": "日数字窄槽",
                    "method": f"{label} {preprocessing}整行识别",
                    "model": model,
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in rows],
                    "parsed_components": [str(value) for value in sorted(values)],
                }
            )
    return variants


def _recognize_adaptive_day_slot_variants(
    day_view: dict[str, Path],
) -> list[dict]:
    """Recognize a shifted day crop on raw and color-suppressed images."""
    try:
        from .paddle_ocr import recognize_line
    except Exception:
        return []
    variants = []
    for preprocessing, path in (
        ("原始裁剪", day_view.get("original")),
        ("最大通道去彩色", day_view.get("processed")),
    ):
        if path is None:
            continue
        for model, label in (("mobile", "Mobile"), ("server", "Server")):
            try:
                rows = recognize_line(path, model_variant=model)
            except Exception:
                rows = []
            values = {
                parsed
                for row in rows
                if (parsed := _parse_adaptive_day_slot(row.text)) is not None
            }
            variants.append(
                {
                    "slot": "自适应日数字槽",
                    "method": f"{label} {preprocessing}整行识别",
                    "model": model,
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in rows],
                    "parsed_components": [str(value) for value in sorted(values)],
                }
            )
    return variants
