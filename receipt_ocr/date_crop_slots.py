"""Gate and coordinate fixed date-slot evidence collection."""

from .date_crop_state import DateCropRun, DateSlotProbe
from .parser import find_receipt_date, parse_date, estimate_date_confidence
from .date_evidence import (
    _required_month_slot_conflict_prefilter_from_artifacts,
    _white_day_conflict_prefilter_from_artifacts,
)
from .date_crop_slot_collection import _prepare_slot_probe, _collect_slot_variants
from .date_crop_slot_selection import _select_slot_date, _recover_unparsed_slot_date
from .date_crop_slot_output import _publish_slot_probe


def probe_date_slots(
    run: DateCropRun, low_confidence_audit_base_rows, recognize_line
) -> None:
    # Fixed-template slot audit.  When every established
    # whole-line path fails, split the printed ``20 年 月 日``
    # row into a full-year view and a wider month/day view.
    # The candidate is constructed only from OCR-owned
    # components: both Mobile and Server line recognizers must
    # agree on an explicit month/day, and both detector models
    # must agree on a complete four-digit year.  Even then the
    # result is normally capped at 25% because every view
    # comes from one physical row. A later narrow path may
    # promote a required-date match only when Mobile and
    # Server recover every component from both approved
    # color-suppressed variants and no other date exists.
    current_slot_date, _ = find_receipt_date(
        low_confidence_audit_base_rows, run.required_text
    )
    required_for_slot = parse_date(run.required_text)
    slot_confidence = estimate_date_confidence(
        low_confidence_audit_base_rows,
        run.required_text,
        current_slot_date,
    )
    month_conflict_prefilter = _required_month_slot_conflict_prefilter_from_artifacts(
        run.artifacts, run.required_text
    )
    white_day_conflict_prefilter = _white_day_conflict_prefilter_from_artifacts(
        run.artifacts, run.required_text
    )
    if (
        required_for_slot is not None
        and (
            current_slot_date in {None, required_for_slot}
            or month_conflict_prefilter is not None
            or white_day_conflict_prefilter is not None
        )
        and slot_confidence < 0.72
    ):
        tight_entry = next(
            (
                item
                for item in run.date_crop_entries
                if item.get("crop_key") == "tight" and not item.get("audit_only")
            ),
            None,
        )
        if tight_entry is not None:
            probe = DateSlotProbe(
                tight_entry=tight_entry,
                white_day_conflict_prefilter=white_day_conflict_prefilter,
            )
            _prepare_slot_probe(run, probe)
            _collect_slot_variants(run, probe, recognize_line)
            _select_slot_date(run, probe)
            _recover_unparsed_slot_date(run, probe)
            _publish_slot_probe(run, probe)
