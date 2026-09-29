"""Gate supporting observations without performing OCR."""
from receipt_ocr.domain.parsing import parse_date, parse_receipt_date
from ..contracts import DateCropRun, DateCropRegion

def _collect_pending_region_evidence(run: DateCropRun, region: DateCropRegion) -> None:
    if (
        run.secondary_ocr_backend
        and region.evidence.accepted_line_rows
        and not region.audit_only
    ):
        required = parse_date(run.required_text)
        strict_line_dates = [
            parse_date(row.text)
            for row in region.evidence.accepted_line_rows
            if parse_date(row.text) is not None
        ]
        no_requirement_consensus = bool(
            run.allow_strict_date_without_requirement
            and required is None
            and len(strict_line_dates) >= 2
            and len(set(strict_line_dates)) == 1
        )
        corroborated = (
            no_requirement_consensus
            or region.evidence.cross_model_month_day_confirmed
            or any(
                required is not None
                and parse_receipt_date(row.text, required) == required
                for row in region.evidence.variant_rows
                + region.evidence.secondary_variant_rows
            )
        )
        # In Hybrid mode a matching recognition-only line is
        # supporting evidence, not an independent verdict. The
        # same model can consistently confuse a handwritten 7
        # with the required 9 on both clean variants.
        if not corroborated:
            run.pending_line_evidence.append(
                {
                    "rows": list(region.evidence.accepted_line_rows),
                    "variants": region.evidence.line_variants,
                    "line_raw": region.images.line_raw,
                    "line_recognition": region.images.line_color_clean,
                    "line_box": region.images.line_box,
                    "region_box": (
                        region.images.x,
                        region.images.y,
                        region.images.width,
                        region.images.height,
                    ),
                    "tight": region.tight,
                }
            )
            region.evidence.accepted_line_rows = []
            for item in region.evidence.line_variants:
                item["accepted_texts"] = []
                item["acceptance_note"] = "仅日期行证据，未用于自动判定"
    # A strict non-matching date read directly from the Mobile
    # recognition-only line is still genuine Mobile evidence.
    # Keep it pending for the same cross-model/cross-geometry
    # audit used by detector output below.  It is not accepted on
    # its own: the real 7→9 handwriting regression demonstrates
    # that one high-confidence line reading can be wrong.
    if run.secondary_ocr_backend and not region.audit_only:
        required = parse_date(run.required_text)
        strict_line_mismatch_rows = [
            row
            for row in region.evidence.line_rows
            if row.confidence >= 0.72
            and (parsed := parse_date(row.text)) is not None
            and parsed != required
        ]
        if strict_line_mismatch_rows:
            run.pending_mismatch_evidence.append(
                {
                    "rows": strict_line_mismatch_rows,
                    "variants": region.evidence.line_variants,
                    "line_box": region.images.line_box,
                    "region_box": (
                        region.images.x,
                        region.images.y,
                        region.images.width,
                        region.images.height,
                    ),
                    "tight": region.tight,
                    "coordinate_space": "line",
                }
            )
    if run.secondary_ocr_backend and not region.audit_only:
        required = parse_date(run.required_text)
        mismatch_rows = [
            row
            for row in region.evidence.secondary_raw_rows
            if (parsed := parse_receipt_date(row.text, required)) is not None
            and parsed != required
        ]
        if mismatch_rows:
            run.pending_mismatch_evidence.append(
                {
                    "rows": mismatch_rows,
                    "secondary_variants": region.evidence.secondary_ocr_variants,
                    "variants": region.evidence.secondary_ocr_variants,
                    "line_variants": region.evidence.line_variants,
                    "line_raw": region.images.line_raw,
                    "line_recognition": region.images.line_color_clean,
                    "line_box": region.images.line_box,
                    "region_box": (
                        region.images.x,
                        region.images.y,
                        region.images.width,
                        region.images.height,
                    ),
                    "tight": region.tight,
                    "coordinate_space": "region",
                }
            )

