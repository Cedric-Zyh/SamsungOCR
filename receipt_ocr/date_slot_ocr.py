"""Collect optional Vision and Paddle evidence from date lines and slots."""

from __future__ import annotations
from datetime import date
from pathlib import Path
from .parser import parse_date
from .ocr_types import TextObservation
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
            parsed for row in rows if (parsed := parse_date(row.text)) is not None
        }
        date_sets.append(values)
        variants.append(
            {
                "preprocessing": (f"原日期行白底标准化 Vision {label} 人工候选"),
                "ocr_texts": [row.text for row in rows],
                "accepted_texts": [],
                "acceptance_note": (
                    "三种 Vision 语言配置必须输出同一完整日期；"
                    "同一系统模型证据仅供人工复核"
                ),
            }
        )
    common = set.intersection(*date_sets) if date_sets else set()
    if len(common) == 1:
        accepted = next(iter(common))
        for variant in variants:
            variant["accepted_texts"] = [
                text for text in variant["ocr_texts"] if parse_date(text) == accepted
            ]
    return variants, common
