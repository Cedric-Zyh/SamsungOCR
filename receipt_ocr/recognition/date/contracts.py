"""Typed state and explicit OCR dependencies for one date-crop run."""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from enum import Enum
from receipt_ocr.domain.ocr import TextObservation


class DateInputKind(str, Enum):
    ORIGINAL = "original"
    COLOR_CLEAN = "color_clean"
    FRAME_CLEAN = "frame_clean"
    TABLE_CLEAN = "table_clean"
    SLOT_CLEAN = "slot_clean"


class DateInputRole(str, Enum):
    DISPLAY = "display"
    DECISION = "decision"
    AUDIT = "audit"


@dataclass(frozen=True)
class PreparedDateInput:
    """Image provenance and permitted use, independent of a UI label."""

    id: str
    region_id: str
    path: Path
    kind: DateInputKind
    role: DateInputRole
    label: str
    coordinate_space: str = "line"

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "kind", DateInputKind(self.kind))
        object.__setattr__(self, "role", DateInputRole(self.role))
        if self.kind == DateInputKind.ORIGINAL and self.role != DateInputRole.DISPLAY:
            raise ValueError("日期原图只能展示，不能作为 OCR 输入")

    @property
    def ocr_input(self) -> bool:
        return self.role != DateInputRole.DISPLAY


@dataclass(frozen=True)
class PreparedDate:
    region_id: str
    inputs: tuple[PreparedDateInput, ...] = ()

    def for_ocr(self, *, coordinate_space: str) -> tuple[PreparedDateInput, ...]:
        return tuple(item for item in self.inputs
                     if item.ocr_input and item.coordinate_space == coordinate_space)


@dataclass(frozen=True)
class DateComponents:
    year: int | None = None
    month: int | None = None
    day: int | None = None


@dataclass(frozen=True)
class DateRead:
    """One OCR observation, retaining raw text and actual model identity."""

    input_id: str
    provider: str
    model: str
    role: DateInputRole
    observation: TextObservation
    text: str = ""
    components: DateComponents = field(default_factory=DateComponents)


@dataclass(frozen=True)
class DateOcrResult:
    input: PreparedDateInput
    provider: str
    model: str
    reads: tuple[DateRead, ...] = ()
    status: str = "empty"
    error: str = ""


@dataclass(frozen=True)
class DateRecognitionResult:
    """Structured recognition output; the legacy API projects rows/artifacts."""

    rows: tuple[TextObservation, ...] = ()
    artifacts: tuple[dict, ...] = ()
    inputs: tuple[PreparedDateInput, ...] = ()
    reads: tuple[DateRead, ...] = ()

    def as_legacy(self) -> tuple[list, list[dict]]:
        return list(self.rows), list(self.artifacts)


@dataclass(frozen=True)
class DateCropServices:
    save_receipt_date_crop: Callable
    save_date_line_crop: Callable
    recognize_text: Callable
    backend_label: Callable


@dataclass
class DateCropRun:
    source: Path
    anchor_y: float
    required_text: str
    artifact_dir: str | Path | None
    artifact_url_prefix: str
    ocr_backend: str
    # Retained only as an input-shape marker for callers constructing state;
    # the compact date pipeline deliberately never reads or routes it.
    secondary_ocr_backend: str | None
    allow_strict_date_without_requirement: bool
    creation_text: str
    temp_dir: str | Path
    services: DateCropServices
    output: list[TextObservation] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    pending_line_evidence: list[dict] = field(default_factory=list)
    pending_mismatch_evidence: list[dict] = field(default_factory=list)
    date_crop_entries: list[dict] = field(default_factory=list)
    # Decision-grade observations from the only approved (tight) crop.
    # Other date geometries are not generated in the current recognition flow.
    primary_rows: list[TextObservation] = field(default_factory=list)
    custom_words: list[str] = field(default_factory=list)
    inputs: list[PreparedDateInput] = field(default_factory=list)
    reads: list[DateRead] = field(default_factory=list)


@dataclass
class DateCropImages:
    crop: Path | None = None
    raw: Path | None = None
    color_clean: Path | None = None
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    line_raw: Path | None = None
    line_color_clean: Path | None = None
    line_table_clean: Path | None = None
    line_positioned_frame_clean: Path | None = None
    line_box: tuple[float, float, float, float] | None = None
    line_table_clean_upscaled: Path | None = None
    line_autocontrast_upscaled: Path | None = None
    line_max_channel_upscaled: Path | None = None
    line_otsu_upscaled: Path | None = None
    line_white_standardized: Path | None = None
    upper_line_raw: Path | None = None
    upper_line_color_clean: Path | None = None
    upper_line_box: tuple[float, float, float, float] | None = None


@dataclass
class DateRegionEvidence:
    reads: list[DateRead] = field(default_factory=list)
    ocr_results: list[DateOcrResult] = field(default_factory=list)
    variant_rows: list[TextObservation] = field(default_factory=list)
    ocr_variants: list[dict] = field(default_factory=list)
    secondary_variant_rows: list[TextObservation] = field(default_factory=list)
    secondary_raw_rows: list[TextObservation] = field(default_factory=list)
    secondary_ocr_variants: list[dict] = field(default_factory=list)
    line_backend: str = ""
    line_rows: list[TextObservation] = field(default_factory=list)
    accepted_line_rows: list[TextObservation] = field(default_factory=list)
    # OCR rows from the outer-frame-cleaned date line.  They are kept separate
    # from the legacy table-line probe while the raw thumbnail stays audit-only.
    display_line_rows: list[TextObservation] = field(default_factory=list)
    line_variants: list[dict] = field(default_factory=list)
    # OCR shown under derivative images only; never consumed by date rules.
    display_line_variants: list[dict] = field(default_factory=list)
    # A component-wise reading from the same compact line.  It is built only
    # from OCR-owned year/month/day votes and a private narrow day crop; no
    # required-date value is copied into it.
    component_candidate: str = ""
    component_candidate_confidence: float = 0.0
    cross_model_month_day_confirmed: bool = False
    far_lower_cross_model_date: date | None = None
    far_lower_server_variants: list[dict] = field(default_factory=list)
    far_lower_padded_line: Path | None = None
    far_lower_padded_variants: list[dict] = field(default_factory=list)
    far_lower_cross_model_mode: str = ""
    far_lower_confirmed_rows: list[TextObservation] = field(default_factory=list)


@dataclass
class DateCropRegion:
    crop_key: str
    tight: bool
    anchor_shift: float
    audit_only: bool
    images: DateCropImages = field(default_factory=DateCropImages)
    evidence: DateRegionEvidence = field(default_factory=DateRegionEvidence)
    prepared: PreparedDate | None = None


@dataclass
class DateSlotProbe:
    tight_entry: dict | None = None
    white_day_conflict_prefilter: dict | None = None
    slot_views: dict = field(default_factory=dict)
    year_view: dict = field(default_factory=dict)
    month_context_view: dict = field(default_factory=dict)
    day_context_view: dict = field(default_factory=dict)
    adaptive_day_view: dict = field(default_factory=dict)
    month_day_view: dict = field(default_factory=dict)
    month_digit_view: dict = field(default_factory=dict)
    year_path: Path | None = None
    month_day_path: Path | None = None
    month_digit_path: Path | None = None
    slot_variants: list[dict] = field(default_factory=list)
    safe_year_variants: list[dict] = field(default_factory=list)
    safe_month_day_variants: list[dict] = field(default_factory=list)
    server_component_variants: list[dict] = field(default_factory=list)
    server_strict_candidate: date | None = None
    truncated_day_candidate: date | None = None
    adaptive_day_variants: list[dict] = field(default_factory=list)
    missing_month_component_prefilter: dict | None = None
    month_day_by_model: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    white_day_component_variants: list[dict] = field(default_factory=list)
    accepted_slot_date: date | None = None
    slot_candidate_source: str = ""
    reliable_slot_date: date | None = None
    server_component_date: date | None = None
    white_day_audit_date: date | None = None
    same_geometry_missing_month_date: date | None = None
    slot_component_candidate: date | None = None
