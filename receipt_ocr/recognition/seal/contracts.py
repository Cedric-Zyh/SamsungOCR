"""Explicit state shared by the ordered local-seal evidence stages.

Each region and secondary candidate gets a fresh evidence object. Output artifacts
and candidate dictionaries retain their established formats for existing users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from receipt_ocr.domain.ocr import TextObservation


@dataclass(frozen=True)
class PreparedVariant:
    """One image variant produced before OCR."""

    name: str
    path: Path
    purpose: str = ""


@dataclass(frozen=True)
class PreparedSeal:
    """Shape-independent handoff from preprocessing to OCR."""

    index: int
    shape: str
    variants: tuple[PreparedVariant, ...] = ()
    orientation: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OcrRead:
    """Provider-neutral OCR result passed to post-processing."""

    text: str
    provider: str = ""
    variant: str = ""
    confidence: float | None = None
    source: Path | None = None


# Explicit result name for callers that prefer the stage terminology.
SealOcrResult = OcrRead


@dataclass(frozen=True)
class SealResult:
    """Stable result object produced after OCR aggregation and matching."""

    status: str
    text: str = ""
    confidence: float = 0.0
    shape: str = ""
    orientation: dict = field(default_factory=dict)
    reads: tuple[OcrRead, ...] = ()
    reasons: tuple[str, ...] = ()
    artifacts: tuple[dict, ...] = ()


@dataclass(frozen=True)
class SealRequest:
    source: Path
    rows: list
    artifact_dir: str | Path | None
    artifact_url_prefix: str
    ocr_backend: str
    secondary_ocr_backend: str | None
    requirement: str
    footer_anchor_y: float | None
    orientation_resolved_indices: frozenset[int] = frozenset()
    orientation_mode: str = "polygon"


@dataclass
class SealReadCollection:
    texts: list[str] = field(default_factory=list)
    reads: list["OcrRead"] = field(default_factory=list)
    prepared: list[PreparedSeal] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    overlapping_secondary_reads: list[dict] = field(default_factory=list)


@dataclass
class RegionEvidence:
    prepared: PreparedSeal | None = None
    shape: str = "round"
    region_texts: list[str] = field(default_factory=list)
    rectangular: bool = False
    elliptical: bool = False
    original: Path | None = None
    whole_text: str = ""
    isolated: Path | None = None
    color_isolated: Path | None = None
    color_isolated_oriented: Path | None = None
    ellipse_normalized: Path | None = None
    orientation: dict = field(default_factory=dict)
    round_type_band: Path | None = None
    round_type_band_texts: list[str] = field(default_factory=list)
    code_line: Path | None = None
    code_line_texts: list[str] = field(default_factory=list)
    crop_text: str = ""
    color_isolated_texts: list[str] = field(default_factory=list)
    oriented_texts: list[str] = field(default_factory=list)
    orientation_anchor_text: str = ""
    unwrapped: Path | None = None
    unwrapped_rotated: Path | None = None
    color_isolated_rotations: Path | None = None
    unwrapped_bands: list[Path] = field(default_factory=list)
    unwrap_texts: list[str] = field(default_factory=list)
    rotated: Path | None = None
    rotated_texts: list[str] = field(default_factory=list)
    secondary_original_texts: list[str] = field(default_factory=list)
    secondary_color_isolated_texts: list[str] = field(default_factory=list)
    secondary_crop_texts: list[str] = field(default_factory=list)
    secondary_unwrap_texts: list[str] = field(default_factory=list)
    secondary_rotated_texts: list[str] = field(default_factory=list)
    secondary_code_line_texts: list[str] = field(default_factory=list)
    original_safe_for_matching: bool = False
    same_region_reconstructed_text: str = ""
    combined_text: str = ""


@dataclass
class SecondaryReadPlan:
    preliminary: dict = field(default_factory=dict)
    preliminary_company_conflict: bool = False
    clipped_prefix_secondary_recheck: bool = False
    explicit_stamp_type: bool = False
    branch_stamp_rotation_route: bool = False
    numbered_service_stamp: bool = False
    dense_partitioned_service_organization: bool = False
    robust_round_shop: bool = False
    fragmented_local_service_center: bool = False
    should_secondary_read: bool = False
    ranked_candidates: list[dict] = field(default_factory=list)
    overlapping_repair_route: bool = False
    company_only_requirement: bool = False
    secondary_limit: int = 0
    # Resolved by ``secondary_read_policy``: which provider actually reads the
    # colour-isolated derivatives, and whether the bands are read by a
    # genuinely different model tier.
    secondary_backend: str | None = None
    secondary_band_backend: str | None = None
    secondary_skip_reason: str = ""
    secondary_mode: str = "auto"


@dataclass
class SecondaryReadEvidence:
    dense_partitioned_candidate: bool = False
    secondary_texts: list[str] = field(default_factory=list)
    round_type_band: Path | None = None
    round_type_band_rows: list[TextObservation] = field(default_factory=list)
    robust_unwrapped: Path | None = None
    robust_band_paths: list[Path] = field(default_factory=list)
    robust_line_texts: list[str] = field(default_factory=list)
    robust_line_variants: list[dict] = field(default_factory=list)
    secondary_paths: list[tuple[str, Path | None]] = field(default_factory=list)
    unwrapped_band_paths: list[Path] = field(default_factory=list)
    secondary_variant_texts: dict[str, list[str]] = field(default_factory=dict)
    robust_reader_texts: list[str] = field(default_factory=list)
    robust_shared_suffix: str = ""
    reconstructed_one_error_type: str = ""
    reconstructed_partitioned_service: str = ""
    combined_secondary_text: str = ""
    secondary_read_used_for_matching: bool = False

