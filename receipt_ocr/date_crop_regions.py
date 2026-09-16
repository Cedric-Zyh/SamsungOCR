"""Recognize region and line variants, retaining unconfirmed evidence."""

from __future__ import annotations
from .parser import parse_date, parse_receipt_date
from .ocr_types import TextObservation
from .date_evidence import _conflicting_receipt_dates, _trailing_numeric_month_day
from .date_crop_state import DateCropRun, DateCropRegion


def _recognize_region_variants(run: DateCropRun, region: DateCropRegion) -> None:
    region.evidence.variant_rows = []
    region.evidence.ocr_variants = []
    region.evidence.secondary_variant_rows = []
    region.evidence.secondary_raw_rows = []
    region.evidence.secondary_ocr_variants = []
    # Table-line removal can help printed dates but occasionally
    # erases thin handwritten strokes. Keep all three variants as
    # independent evidence instead of committing to one transform.
    for preprocessing, candidate in (
        ("去印章色", region.images.color_clean),
        ("原始裁剪", region.images.raw),
        ("去表格线", region.images.crop),
    ):
        try:
            candidate_rows = run.services.recognize_text(
                candidate,
                backend=run.ocr_backend,
                min_text_height=0.02,
                custom_words=[] if region.audit_only else run.custom_words,
            )
        except Exception:
            candidate_rows = []
        region.evidence.variant_rows.extend(candidate_rows)
        region.evidence.ocr_variants.append(
            {
                "preprocessing": preprocessing,
                "ocr_texts": [row.text for row in candidate_rows],
            }
        )
        if run.secondary_ocr_backend:
            try:
                secondary_rows = run.services.recognize_text(
                    candidate,
                    backend=run.secondary_ocr_backend,
                    min_text_height=0.015,
                )
            except Exception:
                secondary_rows = []
            region.evidence.secondary_raw_rows.extend(secondary_rows)
            required = parse_date(run.required_text)
            # Secondary OCR is supporting evidence in Hybrid mode.
            # A lone alternative date may be a real mismatch or a
            # one-glyph OCR error, so do not let it auto-reject the
            # receipt.  Only retain evidence that independently
            # parses to the printed required date; native Paddle
            # mode remains free to recognize genuine mismatches.
            accepted_secondary = (
                []
                if region.audit_only
                else [
                    row
                    for row in secondary_rows
                    if (
                        required is not None
                        and parse_receipt_date(row.text, required) == required
                    )
                    or (
                        run.allow_strict_date_without_requirement
                        and required is None
                        and parse_date(row.text) is not None
                    )
                ]
            )
            region.evidence.secondary_variant_rows.extend(accepted_secondary)
            region.evidence.secondary_ocr_variants.append(
                {
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in secondary_rows],
                    "accepted_texts": [row.text for row in accepted_secondary],
                }
            )


def _recognize_region_lines(run: DateCropRun, region: DateCropRegion) -> None:
    # A precise one-line crop bypasses text detection.  This is
    # especially useful for adjacent handwritten digits such as
    # 21/22, which the detector may otherwise merge into one 2.
    # The Server pipeline has already run detection+recognition on
    # these date candidates. Loading a second Server recognition-
    # only predictor can exceed local memory, so the detector-
    # bypass path is reserved for the lightweight Mobile model.
    region.evidence.line_backend = (
        "paddle"
        if run.secondary_ocr_backend == "paddle" or run.ocr_backend == "paddle"
        else ""
    )
    region.evidence.line_rows = []
    region.evidence.accepted_line_rows = []
    region.evidence.line_variants = []
    region.evidence.cross_model_month_day_confirmed = False
    if region.evidence.line_backend:
        from .paddle_ocr import recognize_line

        model_variant = (
            "server" if region.evidence.line_backend == "paddle_server" else "mobile"
        )
        for preprocessing, candidate, strict_only in (
            ("日期行原图", region.images.line_raw, False),
            ("日期行去印章色", region.images.line_color_clean, False),
            ("日期行去表格线", region.images.line_table_clean, True),
        ):
            try:
                current_rows = recognize_line(candidate, model_variant=model_variant)
            except Exception:
                current_rows = []
            region.evidence.line_rows.extend(current_rows)
            if run.secondary_ocr_backend:
                required = parse_date(run.required_text)
                accepted = [
                    row
                    for row in current_rows
                    if (
                        not strict_only
                        and (
                            required is not None
                            and parse_receipt_date(row.text, required) == required
                        )
                    )
                    or (
                        run.allow_strict_date_without_requirement
                        and required is None
                        and parse_date(row.text) is not None
                    )
                    or (
                        strict_only
                        and required is not None
                        and parse_date(row.text) == required
                    )
                ]
            else:
                accepted = current_rows
            region.evidence.accepted_line_rows.extend(accepted)
            region.evidence.line_variants.append(
                {
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in current_rows],
                    "accepted_texts": [row.text for row in accepted],
                }
            )
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
            for candidate in (
                region.images.upper_line_raw,
                region.images.upper_line_color_clean,
            ):
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
                        region.images.upper_line_raw, model_variant="server"
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
        # layouts; original readings remain in artifact evidence.
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
                    region.images.line_raw, model_variant="server"
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
        # with the required 9 on both raw/clean variants.
        if not corroborated:
            run.pending_line_evidence.append(
                {
                    "rows": list(region.evidence.accepted_line_rows),
                    "variants": region.evidence.line_variants,
                    "line_raw": region.images.line_raw,
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
