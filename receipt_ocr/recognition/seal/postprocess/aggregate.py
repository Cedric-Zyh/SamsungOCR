"""Normalize and aggregate provider reads before the business matcher."""

from __future__ import annotations

from collections.abc import Iterable

from receipt_ocr.domain.parsing import normalize_text

from ..contracts import OcrRead


def normalize_reads(reads: Iterable[OcrRead]) -> list[OcrRead]:
    """Drop empty reads and normalize only the comparison representation."""

    result: list[OcrRead] = []
    seen: set[tuple[str, str, str]] = set()
    for read in reads:
        text = str(read.text or "").strip()
        normalized = normalize_text(text)
        key = (normalized, read.provider, read.variant)
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(
            OcrRead(
                text=text,
                confidence=read.confidence,
                provider=read.provider,
                variant=read.variant,
                source=read.source,
            )
        )
    return result


def aggregate_texts(reads: Iterable[OcrRead]) -> list[str]:
    """Return stable display/matching candidates in read order."""

    return list(dict.fromkeys(read.text for read in normalize_reads(reads)))


__all__ = ["aggregate_texts", "normalize_reads"]
