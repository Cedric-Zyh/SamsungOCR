"""Collect the single approved compact date region."""

from receipt_ocr.recognition.date.contracts import DateCropRun, DateCropRegion
from receipt_ocr.imaging.processing import DateCropOutOfRange
from receipt_ocr.recognition.date.preprocess.inputs import _prepare_date_region
from receipt_ocr.recognition.date.ocr.regions import (
    _recognize_region_variants,
    _recognize_region_lines,
)
from receipt_ocr.recognition.date.audit.artifacts import _publish_date_region


def collect_date_regions(run: DateCropRun) -> None:
    # Date recognition is intentionally limited to the compact date cell.
    # Do not create the historical wide/lower audit crops: they add OCR
    # evidence that is outside the currently approved recognition scope and
    # make those regions appear in the review UI.
    region = DateCropRegion("tight", True, 0.0, False)
    try:
        _prepare_date_region(run, region)
    except DateCropOutOfRange:
        return
    _recognize_region_variants(run, region)
    _recognize_region_lines(run, region)
    _publish_date_region(run, region)
