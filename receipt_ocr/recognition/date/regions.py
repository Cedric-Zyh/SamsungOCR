"""Collect the single approved date region."""

from receipt_ocr.recognition.date.contracts import DateCropRun, DateCropRegion
from receipt_ocr.imaging.contracts import DateCropOutOfRange
from receipt_ocr.recognition.date.preprocess.inputs import _prepare_date_region
from receipt_ocr.recognition.date.ocr.regions import (
    _recognize_region_variants,
    _recognize_region_lines,
)
from receipt_ocr.recognition.date.audit.artifacts import _publish_date_region


def collect_date_regions(run: DateCropRun) -> None:
    # Date recognition uses one page location. Do not create historical
    # alternate crops or expose a second date position in the review UI.
    region = DateCropRegion("tight", True, 0.0, False)
    try:
        _prepare_date_region(run, region)
    except DateCropOutOfRange:
        return
    _recognize_region_variants(run, region)
    _recognize_region_lines(run, region)
    _publish_date_region(run, region)
