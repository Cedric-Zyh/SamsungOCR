"""Business-facing seal result construction."""

from __future__ import annotations

from collections.abc import Iterable

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict

from ..contracts import OcrRead, SealResult
from .aggregate import aggregate_texts, normalize_reads


def decide(requirement: str, reads: Iterable[OcrRead], **metadata) -> SealResult:
    """Compare normalized OCR reads with the requested seal text."""

    normalized_reads = normalize_reads(reads)
    texts = aggregate_texts(normalized_reads)
    comparison = compare_seal_text_strict(requirement, texts)
    return SealResult(
        status=comparison.get("status", "未识别"),
        text=comparison.get("recognized", ""),
        confidence=float(comparison.get("confidence", 0.0) or 0.0),
        shape=str(metadata.get("shape", "")),
        orientation=dict(metadata.get("orientation") or {}),
        reads=tuple(normalized_reads),
        reasons=tuple(metadata.get("reasons") or ()),
        artifacts=tuple(metadata.get("artifacts") or ()),
    )


__all__ = ["decide"]
