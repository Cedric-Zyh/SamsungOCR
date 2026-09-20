"""Collect the single approved compact date region."""

from .date_crop_state import DateCropRun, DateCropRegion
from .image_processing import DateCropOutOfRange
from .date_crop_preparation import _prepare_date_region
from .date_crop_regions import (
    _recognize_region_variants,
    _recognize_region_lines,
    _collect_pending_region_evidence,
)
from .date_crop_region_output import _confirm_far_lower_region, _publish_date_region


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
    _collect_pending_region_evidence(run, region)
    _confirm_far_lower_region(run, region)
    _publish_date_region(run, region)
