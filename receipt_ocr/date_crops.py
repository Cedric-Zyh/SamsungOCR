"""Collect date OCR evidence through ordered region, confirmation and audit steps."""

from __future__ import annotations
import tempfile
from contextlib import nullcontext
from pathlib import Path
from .execution import timed
from .image_processing import save_receipt_date_crop
from .ocr_backends import backend_label, recognize_text
from .date_evidence import (
    _save_date_line_crop,
    _recognize_date_slot_with_vision,
    _recognize_date_line_vision_consensus,
)
from .date_crop_state import DateCropRun, DateCropServices
from .date_crop_workflow import collect_date_regions
from .date_crop_audits import run_low_confidence_date_audit
from .date_crop_confirmation import (
    _confirm_repeated_date_lines,
    _confirm_cross_geometry_dates,
    _collect_original_line_mismatches,
    _confirm_pending_date_mismatches,
)
from .date_crop_color_confirmation import (
    _confirm_color_suppressed_dates,
)
from .date_crop_final_audits import (
    _confirm_missing_year_separator,
    _confirm_nondestructive_cross_year,
    _audit_original_color_handwriting,
)


@timed("date_recognition")
def _recognize_receipt_date(
    source: Path,
    anchor_y: float,
    required_text: str,
    artifact_dir: str | Path | None,
    artifact_url_prefix: str,
    ocr_backend: str,
    secondary_ocr_backend: str | None = None,
    allow_strict_date_without_requirement: bool = False,
    creation_text: str = "",
) -> tuple[list, list[dict]]:
    context = (
        nullcontext(str(Path(artifact_dir) / "date"))
        if artifact_dir
        else tempfile.TemporaryDirectory(prefix="receipt-date-")
    )
    with context as temp_dir:
        Path(temp_dir).mkdir(parents=True, exist_ok=True)
        run = DateCropRun(
            source=source,
            anchor_y=anchor_y,
            required_text=required_text,
            artifact_dir=artifact_dir,
            artifact_url_prefix=artifact_url_prefix,
            ocr_backend=ocr_backend,
            secondary_ocr_backend=secondary_ocr_backend,
            allow_strict_date_without_requirement=allow_strict_date_without_requirement,
            creation_text=creation_text,
            temp_dir=temp_dir,
            services=DateCropServices(
                save_receipt_date_crop=save_receipt_date_crop,
                save_date_line_crop=_save_date_line_crop,
                recognize_text=recognize_text,
                backend_label=backend_label,
                recognize_date_slot_with_vision=_recognize_date_slot_with_vision,
                recognize_date_line_vision_consensus=_recognize_date_line_vision_consensus,
            ),
        )
        if run.required_text:
            parts = run.required_text.split("-")
            if len(parts) == 3:
                run.custom_words.append(
                    f"{parts[0]}年{int(parts[1])}月{int(parts[2])}日"
                )
            run.custom_words.append(run.required_text)
        collect_date_regions(run)
        _confirm_repeated_date_lines(run)
        _confirm_cross_geometry_dates(run)
        _confirm_color_suppressed_dates(run)
        _collect_original_line_mismatches(run)
        _confirm_pending_date_mismatches(run)
        run_low_confidence_date_audit(run)
        _confirm_missing_year_separator(run)
        _confirm_nondestructive_cross_year(run)
        _audit_original_color_handwriting(run)
        return run.output, run.artifacts
