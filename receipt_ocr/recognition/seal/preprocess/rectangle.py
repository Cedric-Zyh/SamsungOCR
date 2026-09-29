"""Rectangular stamp preparation."""

from __future__ import annotations

from pathlib import Path

from receipt_ocr.imaging.processing import SealRegion

def prepare(source: Path, regions: list[SealRegion], destination: str | Path):
    """Correct page/rectangle orientation while preserving page coordinates."""

    from . import orientation

    return orientation.prepare_rectangles(source, regions, destination)


__all__ = ["prepare"]
