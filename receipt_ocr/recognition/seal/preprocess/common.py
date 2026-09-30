"""Shared image-preparation helpers used by all stamp shapes."""

from __future__ import annotations

from pathlib import Path

from receipt_ocr.imaging.contracts import SealRegion

from receipt_ocr.imaging.crops import save_color_isolated_seal, save_region_crop


def crop_common(source: Path, region: SealRegion, destination: Path) -> Path:
    """Write the common source and colour-safe crop used by every shape."""

    save_region_crop(source, destination, region)
    return destination


def color_crop(source: Path, region: SealRegion, destination: Path) -> Path:
    """Write the colour-isolated image handed to shape-specific preparation."""

    save_color_isolated_seal(source, destination, region)
    return destination


__all__ = ["color_crop", "crop_common"]
