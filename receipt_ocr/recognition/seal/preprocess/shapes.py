"""Shape classification and the common prepared-seal contract."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from receipt_ocr.imaging.processing import (
    SealRegion,
    seal_region_is_elliptical,
    seal_region_is_rectangular,
)

from ..contracts import PreparedSeal, PreparedVariant, RegionEvidence


class SealPreprocessor(Protocol):
    """Shape-specific preparation boundary."""

    shape: str

    def supports(self, shape: str) -> bool: ...

    def prepare(self, region: SealRegion, source: Path, destination: Path) -> PreparedSeal: ...


def classify_shape(source: Path, region: SealRegion) -> str:
    """Return one of the three shapes understood by the OCR pipeline."""

    if seal_region_is_rectangular(source, region):
        return "rectangle"
    if seal_region_is_elliptical(source, region):
        return "ellipse"
    return "round"


def prepared_from_evidence(
    index: int,
    shape: str,
    evidence: RegionEvidence,
) -> PreparedSeal:
    """Build the common OCR handoff after shape-specific preparation."""

    paths = (
        ("original", evidence.original, "原始裁剪"),
        ("isolated", evidence.isolated, "黑白隔离"),
        ("color", evidence.color_isolated, "章色隔离"),
        ("oriented", evidence.color_isolated_oriented, "旋正"),
        ("ellipse_normalized", evidence.ellipse_normalized, "椭圆校正"),
        ("type_band", evidence.round_type_band, "章型横向分带"),
        ("unwrapped", evidence.unwrapped, "环形文字展开"),
        ("unwrapped_rotated", evidence.unwrapped_rotated, "展开图旋转对照"),
        ("rotated", evidence.rotated, "矩形章旋转对照"),
    )
    variants = tuple(
        PreparedVariant(name=name, path=Path(path), purpose=purpose)
        for name, path, purpose in paths
        if path is not None and Path(path).is_file()
    )
    return PreparedSeal(
        index=index,
        shape=shape,
        variants=variants,
        orientation=dict(evidence.orientation),
        metadata={
            "orientation_anchor_text": evidence.orientation_anchor_text,
            "rectangular": evidence.rectangular,
            "elliptical": evidence.elliptical,
        },
    )


__all__ = ["SealPreprocessor", "classify_shape", "prepared_from_evidence"]
