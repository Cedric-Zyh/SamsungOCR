"""Run the single-location date OCR pipeline with one explicit path."""

from __future__ import annotations
import tempfile
from contextlib import nullcontext
from pathlib import Path
from receipt_ocr.runtime.execution import timed
from receipt_ocr.imaging.date import save_receipt_date_crop
from receipt_ocr.providers.catalog import backend_label, recognize_text
from receipt_ocr.recognition.date.preprocess.crops import _save_date_line_crop
from receipt_ocr.recognition.date.contracts import (
    DateCropRun,
    DateCropServices,
    DateRecognitionResult,
)
from receipt_ocr.recognition.date.regions import collect_date_regions
from receipt_ocr.recognition.date.postprocess.normalize import normalize_date_only_rows, sanitize_date_artifacts


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
            # The former Mobile/Server audit branch is retired.  Keep the
            # call parameter at the outer boundary for callers that still
            # pass it, but never route it to an OCR provider.
            secondary_ocr_backend=None,
            allow_strict_date_without_requirement=allow_strict_date_without_requirement,
            creation_text=creation_text,
            temp_dir=temp_dir,
            services=DateCropServices(
                save_receipt_date_crop=save_receipt_date_crop,
                save_date_line_crop=_save_date_line_crop,
                recognize_text=recognize_text,
                backend_label=backend_label,
            ),
        )
        collect_date_regions(run)
        # Local date OCR is date-only at the decision boundary.  No required
        # date is copied into OCR output and no secondary-model audit is run.
        run.output = normalize_date_only_rows(run.output)
        result = DateRecognitionResult(
            rows=tuple(run.output),
            artifacts=tuple(sanitize_date_artifacts(run.artifacts)),
            inputs=tuple(run.inputs),
            reads=tuple(run.reads),
        )
        return result.as_legacy()
