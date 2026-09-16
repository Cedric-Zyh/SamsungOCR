"""Collect date regions in order, retaining the lower-crop early exit."""

from .date_crop_state import DateCropRun, DateCropRegion
from .parser import find_receipt_date, parse_date, parse_receipt_date
from .date_crop_preparation import _prepare_date_region
from .date_crop_regions import (
    _recognize_region_variants,
    _recognize_region_lines,
    _collect_pending_region_evidence,
)
from .date_crop_region_output import _confirm_far_lower_region, _publish_date_region


def collect_date_regions(run: DateCropRun) -> None:
    # Most receipts write the date inside the right-most table cell,
    # but some customers place it immediately below/right of the
    # table (for example next to a customer stamp).  Keep the two
    # established table crops and add a vertically shifted crop for
    # that real layout instead of enlarging one crop so much that
    # unrelated printed dates enter the OCR evidence.
    for crop_key, tight, anchor_shift in (
        ("tight", True, 0.0),
        ("wide", False, 0.0),
        ("lower", False, 0.055),
        # Some customers leave the formal date cell blank and write
        # ``2025.8.5`` much farther below the stamp.  This extra crop
        # is audit-only: a strict date is exposed at confidence 0.35
        # for review and can never form an automatic verdict.
        ("far_lower", False, 0.23),
        # A minority of customers stamp near the bottom of the page
        # and handwrite the receiving date beside that stamp.  Keep a
        # second, deeper audit window so the UI shows the evidence;
        # it remains audit-only and therefore cannot auto-pass.
        ("deep_lower", False, 0.31),
    ):
        audit_only = crop_key in {"far_lower", "deep_lower"}
        if crop_key in {"lower", "far_lower", "deep_lower"}:
            already_found, _ = find_receipt_date(run.output, run.required_text)
            required = parse_date(run.required_text)
            normal_line_consensus = (
                sum(
                    1
                    for item in run.pending_line_evidence
                    if required is not None
                    and any(
                        parse_receipt_date(row.text, required) == required
                        for row in item["rows"]
                    )
                )
                >= 2
            )
            if (
                required is not None and already_found == required
            ) or normal_line_consensus:
                # The normal table crops already provided the required
                # date. Avoid the extra lower-crop OCR/model work on
                # ordinary receipts; the extension is a fallback for
                # missing or contradictory table evidence.
                continue
        region = DateCropRegion(crop_key, tight, anchor_shift, audit_only)
        _prepare_date_region(run, region)
        _recognize_region_variants(run, region)
        _recognize_region_lines(run, region)
        _collect_pending_region_evidence(run, region)
        _confirm_far_lower_region(run, region)
        _publish_date_region(run, region)
