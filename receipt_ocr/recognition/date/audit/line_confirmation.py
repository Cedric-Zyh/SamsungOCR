"""Historical component confirmations, separate from ordinary line OCR."""
from receipt_ocr.domain.parsing import parse_date, parse_receipt_date
from receipt_ocr.domain.ocr import TextObservation
from ..postprocess.normalize import _conflicting_receipt_dates, _trailing_numeric_month_day
from ..contracts import DateCropRun, DateCropRegion


def confirm_line_components(run: DateCropRun, region: DateCropRegion) -> None:
    from receipt_ocr.recognition.date.ocr.interface import read_audit_line as recognize_line
    # Do not promote a single-model narrow-slot guess.  In particular,
    # the ambiguous day in this form has produced both ``0`` and ``5``;
    # without an independent model agreement it must remain blank.
    # The upper-row note is promoted only when Mobile and
    # Server independently read the printed required date.
    # One model or one preprocessing variant is deliberately
    # insufficient, preventing the required date from being
    # guessed into an otherwise blank formal date cell.
    required = parse_date(run.required_text)
    if (
        not region.audit_only
        and region.images.upper_line_raw is not None
        and run.secondary_ocr_backend == "paddle"
        and required is not None
    ):
        upper_mobile_rows = []
        for candidate in (region.images.upper_line_color_clean,):
            try:
                upper_mobile_rows.extend(
                    recognize_line(candidate, model_variant="mobile")
                )
            except Exception:
                pass
        matching_mobile = [
            row
            for row in upper_mobile_rows
            if parse_receipt_date(row.text, required) == required
        ]
        upper_server_rows = []
        if matching_mobile:
            try:
                upper_server_rows = recognize_line(
                    region.images.upper_line_color_clean, model_variant="server"
                )
            except Exception:
                upper_server_rows = []
        matching_server = [
            row
            for row in upper_server_rows
            if parse_receipt_date(row.text, required) == required
        ]
        upper_confirmed = bool(matching_mobile and matching_server)
        region.evidence.line_variants.append(
            {
                "preprocessing": "上方手写日期 Mobile/Server 复核",
                "ocr_texts": [row.text for row in upper_mobile_rows]
                + [row.text for row in upper_server_rows],
                "accepted_texts": (
                    [row.text for row in matching_mobile]
                    + [row.text for row in matching_server]
                    if upper_confirmed
                    else []
                ),
                "acceptance_note": (
                    "上方手写日期经 Mobile 与 Server 独立确认"
                    if upper_confirmed
                    else "未形成跨模型一致，不参与自动判定"
                ),
            }
        )
        if upper_confirmed:
            region.evidence.cross_model_month_day_confirmed = True
            normalized = f"{required.year}年{required.month}月" f"{required.day}日"
            for row in (
                max(matching_mobile, key=lambda item: item.confidence),
                max(matching_server, key=lambda item: item.confidence),
            ):
                region.evidence.accepted_line_rows.append(
                    TextObservation(
                        text=normalized,
                        confidence=row.confidence,
                        x=row.x,
                        y=row.y,
                        width=row.width,
                        height=row.height,
                    )
                )
    # Handwritten dates can omit/merge year strokes while
    # leaving a clear month/day (e.g. Mobile ``-820年4月20日``
    # and Server ``802年4月20日``). Do not fill from the required
    # date on one OCR result. Promote only when two different
    # recognition models independently agree on the required
    # month/day and neither exposes a contradictory four-digit
    # year. This applies to both table-line and below-table
    # layouts; raw thumbnails remain audit-only evidence.
    required = parse_date(run.required_text)
    mobile_partial_rows = [
        row
        for row in region.evidence.line_rows
        if required is not None and _trailing_numeric_month_day(row.text, required)
    ]
    if (
        not region.audit_only
        and run.secondary_ocr_backend == "paddle"
        and required is not None
        and mobile_partial_rows
        and not any(
            parse_date(row.text) == required
            for row in region.evidence.accepted_line_rows
        )
    ):
        try:
            server_partial_rows = recognize_line(
                region.images.line_color_clean, model_variant="server"
            )
        except Exception:
            server_partial_rows = []
        matching_server_rows = [
            row
            for row in server_partial_rows
            if _trailing_numeric_month_day(row.text, required)
            and parse_date(row.text) != required
        ]
        conflicting_local_dates = _conflicting_receipt_dates(
            region.evidence.variant_rows
            + region.evidence.secondary_raw_rows
            + region.evidence.line_rows,
            required,
        )
        accepted_server_rows = (
            [] if conflicting_local_dates else matching_server_rows
        )
        region.evidence.line_variants.append(
            {
                "preprocessing": "日期行 Server 大模型月日复核",
                "ocr_texts": [row.text for row in server_partial_rows],
                "accepted_texts": [row.text for row in accepted_server_rows],
                "acceptance_note": (
                    "存在其他可解析日期候选，不参与自动判定"
                    if matching_server_rows and conflicting_local_dates
                    else (
                        "Mobile 与 Server 独立识别的数字月日一致"
                        if matching_server_rows
                        else "未形成跨模型一致的数字月日"
                    )
                ),
            }
        )
        if accepted_server_rows:
            region.evidence.cross_model_month_day_confirmed = True
            normalized = f"{required.year}年{required.month}月{required.day}日"
            for row in (
                max(mobile_partial_rows, key=lambda item: item.confidence),
                max(accepted_server_rows, key=lambda item: item.confidence),
            ):
                region.evidence.accepted_line_rows.append(
                    TextObservation(
                        text=normalized,
                        confidence=row.confidence,
                        x=row.x,
                        y=row.y,
                        width=row.width,
                        height=row.height,
                    )
                )
