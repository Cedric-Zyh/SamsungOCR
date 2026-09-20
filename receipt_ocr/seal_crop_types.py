"""Explicit state shared by the ordered local-seal evidence stages.

Each region and audit candidate gets a fresh evidence object. Output artifacts
and candidate dictionaries retain their established formats for existing users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ocr_types import TextObservation


@dataclass(frozen=True)
class SealCropRequest:
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
class SealEvidenceCollection:
    texts: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    server_candidates: list[dict] = field(default_factory=list)
    overlapping_server_audits: list[dict] = field(default_factory=list)


@dataclass
class SealRegionEvidence:
    region_texts: list[str] = field(default_factory=list)
    rectangular: bool = False
    original: Path | None = None
    whole_text: str = ""
    isolated: Path | None = None
    color_isolated: Path | None = None
    color_isolated_oriented: Path | None = None
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
class SealAuditRoute:
    preliminary: dict = field(default_factory=dict)
    preliminary_company_conflict: bool = False
    clipped_prefix_server_recheck: bool = False
    explicit_stamp_type: bool = False
    branch_stamp_rotation_route: bool = False
    numbered_service_stamp: bool = False
    dense_partitioned_service_organization: bool = False
    robust_round_shop: bool = False
    fragmented_local_service_center: bool = False
    should_server_audit: bool = False
    ranked_candidates: list[dict] = field(default_factory=list)
    overlapping_repair_route: bool = False
    company_only_requirement: bool = False
    audit_limit: int = 0
    # Resolved by ``seal_audit_policy``: which provider actually reads the
    # colour-isolated derivatives, and whether the bands are read by a
    # genuinely different model tier.
    audit_backend: str | None = None
    audit_band_backend: str | None = None
    audit_skip_reason: str = ""
    audit_mode: str = "auto"


@dataclass
class SealAuditEvidence:
    dense_partitioned_candidate: bool = False
    audit_texts: list[str] = field(default_factory=list)
    round_type_band: Path | None = None
    round_type_band_rows: list[TextObservation] = field(default_factory=list)
    robust_unwrapped: Path | None = None
    robust_band_paths: list[Path] = field(default_factory=list)
    robust_mobile_texts: list[str] = field(default_factory=list)
    robust_mobile_variants: list[dict] = field(default_factory=list)
    audit_paths: list[tuple[str, Path | None]] = field(default_factory=list)
    unwrapped_band_paths: list[Path] = field(default_factory=list)
    audit_variant_texts: dict[str, list[str]] = field(default_factory=dict)
    robust_server_texts: list[str] = field(default_factory=list)
    robust_shared_suffix: str = ""
    reconstructed_one_error_type: str = ""
    reconstructed_partitioned_service: str = ""
    combined_audit: str = ""
    server_audit_used_for_matching: bool = False
