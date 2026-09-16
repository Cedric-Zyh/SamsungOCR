"""Confirm pending date rows across independent models and crop geometries."""

from __future__ import annotations
from datetime import date
from .parser import parse_date, parse_receipt_date
from .ocr_types import TextObservation
from .date_evidence import _explicit_trailing_numeric_month_day
from .date_crop_state import DateCropRun


def _confirm_repeated_date_lines(run: DateCropRun) -> None:
    # Recognition-only line OCR may safely corroborate itself across
    # two genuinely different geometric crops. Require evidence from
    # both tight and wide crops, plus two preprocessing observations
    # in at least one crop. A single crop reading the required date is
    # deliberately insufficient (see the handwritten 7→9 regression).
    if run.secondary_ocr_backend and len(run.pending_line_evidence) >= 2:
        required = parse_date(run.required_text)
        evidence = [
            item
            for item in run.pending_line_evidence
            if required is not None
            and any(
                parse_receipt_date(row.text, required) == required
                for row in item["rows"]
            )
        ]
        acceptance_note = ""
        if len(evidence) >= 2 and any(len(item["rows"]) >= 2 for item in evidence):
            acceptance_note = "紧裁与宽裁日期行一致，作为相互印证"
        elif len(evidence) >= 2 and run.secondary_ocr_backend == "paddle":
            # Mobile has independently seen the required month/day in
            # both geometric crops, but only once per crop. Ask the
            # Server recognizer for a strict four-digit confirmation;
            # the Server result alone is never enough to promote it.
            from .paddle_ocr import recognize_line

            for item in evidence:
                try:
                    server_rows = recognize_line(
                        item["line_raw"], model_variant="server"
                    )
                except Exception:
                    server_rows = []
                strict_server_rows = [
                    row
                    for row in server_rows
                    if row.confidence >= 0.82 and parse_date(row.text) == required
                ]
                item["variants"].append(
                    {
                        "preprocessing": "日期行 Server 大模型复核",
                        "ocr_texts": [row.text for row in server_rows],
                        "accepted_texts": [row.text for row in strict_server_rows],
                    }
                )
                if strict_server_rows:
                    item["rows"].extend(strict_server_rows)
                    acceptance_note = "紧裁/宽裁月日一致 + Server 完整日期复核"
                    break
        if acceptance_note:
            for item in evidence:
                lx, ly, lw, lh = item["line_box"]
                x, y, width, height = item["region_box"]
                accepted_texts = []
                for row in item["rows"]:
                    if parse_receipt_date(row.text, required) != required:
                        continue
                    accepted_texts.append(row.text)
                    local = TextObservation(
                        text=row.text,
                        confidence=row.confidence,
                        x=lx + row.x * lw,
                        y=ly + row.y * lh,
                        width=row.width * lw,
                        height=row.height * lh,
                    )
                    run.output.append(
                        TextObservation(
                            text=local.text,
                            confidence=local.confidence,
                            x=x + local.x * width,
                            y=y + local.y * height,
                            width=local.width * width,
                            height=local.height * height,
                        )
                    )
                for variant in item["variants"]:
                    accepted = [
                        text
                        for text in variant.get("ocr_texts", [])
                        if parse_receipt_date(text, required) == required
                    ]
                    variant["accepted_texts"] = accepted
                    variant["acceptance_note"] = (
                        acceptance_note if accepted else "该预处理未形成可接受日期"
                    )


def _confirm_cross_geometry_dates(run: DateCropRun) -> None:
    # One Mobile line result is never enough to certify a required
    # date.  It can, however, be independently corroborated when the
    # Server recognizer reads the same strict four-digit date from the
    # *other* tight/wide geometry.  Requiring both a different model
    # and a different crop preserves the one-crop 7/9 safety rule.
    if run.secondary_ocr_backend == "paddle" and run.date_crop_entries:
        required = parse_date(run.required_text)
        if required is not None:
            from .paddle_ocr import recognize_line

            # Do not let a high-confidence *partial* row prevent the
            # independent audit.  What matters is whether the output
            # already contains a reliable strict four-digit date, not
            # which repaired/partial row wins the candidate ranking.
            needs_confirmation = not any(
                row.confidence >= 0.72 and parse_date(row.text) == required
                for row in run.output
            )
            confirmed = False
            mobile_line_evidence = [
                {
                    "rows": list(crop_entry.get("line_rows") or []),
                    "variants": crop_entry["line_variants"],
                    "line_box": crop_entry["line_box"],
                    "region_box": crop_entry["region_box"],
                    "tight": crop_entry["tight"],
                }
                for crop_entry in run.date_crop_entries
                if not crop_entry.get("audit_only")
            ]
            for mobile_item in mobile_line_evidence if needs_confirmation else []:
                mobile_rows = [
                    row
                    for row in mobile_item["rows"]
                    if parse_date(row.text) == required
                ]
                if not mobile_rows:
                    continue
                for crop_entry in run.date_crop_entries:
                    if (
                        crop_entry.get("audit_only")
                        or crop_entry["tight"] == mobile_item["tight"]
                    ):
                        continue
                    try:
                        server_rows = recognize_line(
                            crop_entry["line_raw"], model_variant="server"
                        )
                    except Exception:
                        server_rows = []
                    strict_server_rows = [
                        row
                        for row in server_rows
                        if row.confidence >= 0.82 and parse_date(row.text) == required
                    ]
                    crop_entry["line_variants"].append(
                        {
                            "preprocessing": "日期行 Mobile/Server 跨几何复核",
                            "ocr_texts": [row.text for row in server_rows],
                            "accepted_texts": [row.text for row in strict_server_rows],
                            "acceptance_note": (
                                "Mobile 与 Server 在紧/宽不同裁剪上读到同一完整日期"
                                if strict_server_rows
                                else "未形成跨模型、跨几何一致日期"
                            ),
                        }
                    )
                    if not strict_server_rows:
                        continue
                    for row in mobile_rows:
                        lx, ly, lw, lh = mobile_item["line_box"]
                        x, y, width, height = mobile_item["region_box"]
                        run.output.append(
                            type(row)(
                                text=row.text,
                                confidence=row.confidence,
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            )
                        )
                    for row in strict_server_rows:
                        lx, ly, lw, lh = crop_entry["line_box"]
                        x, y, width, height = crop_entry["region_box"]
                        run.output.append(
                            type(row)(
                                text=row.text,
                                confidence=row.confidence,
                                x=x + (lx + row.x * lw) * width,
                                y=y + (ly + row.y * lh) * height,
                                width=row.width * lw * width,
                                height=row.height * lh * height,
                            )
                        )
                    for variant in mobile_item["variants"]:
                        accepted = [
                            text
                            for text in variant.get("ocr_texts", [])
                            if parse_date(text) == required
                        ]
                        if accepted:
                            variant["accepted_texts"] = accepted
                            variant["acceptance_note"] = (
                                "与 Server 另一几何裁剪的完整日期一致"
                            )
                    confirmed = True
                    break
                if confirmed:
                    break


def _collect_original_line_mismatches(run: DateCropRun) -> None:
    # A genuine non-matching date is a materially stronger claim than
    # "could not read". In Hybrid mode, promote it only when Mobile's
    # detection pipeline and Server's recognition-only model agree,
    # and the Server supplies a strict four-digit date from the other
    # geometric crop. This recognized 2025-03-06 in a real sample
    # while preserving the review-only outcome for ambiguous 7/9 and
    # 10-like handwriting.
    # A damaged year must not discard an otherwise explicit Mobile
    # month/day.  Admit that weaker Mobile evidence to the mismatch
    # audit only when every valid partial line observation agrees on
    # one non-required date.  It still needs a strict Server date from
    # the *other* geometry below.  This recovers real ``201年4月29日``
    # + ``2025年4月29日`` evidence without promoting samples where the
    # transforms disagree (for example one crop says 1/1, another
    # says 7/1).
    if run.secondary_ocr_backend == "paddle":
        required = parse_date(run.required_text)
        partial_line_evidence: list[tuple[dict, date, list]] = []
        partial_dates: set[date] = set()
        if required is not None:
            for crop_entry in run.date_crop_entries:
                if crop_entry.get("audit_only"):
                    continue
                grouped: dict[date, list] = {}
                for row in crop_entry.get("line_rows") or []:
                    if row.confidence < 0.72 or parse_date(row.text) is not None:
                        continue
                    month_day = _explicit_trailing_numeric_month_day(
                        row.text, required.year
                    )
                    if month_day is None:
                        continue
                    candidate = date(required.year, *month_day)
                    if candidate == required:
                        continue
                    grouped.setdefault(candidate, []).append(row)
                    partial_dates.add(candidate)
                for candidate, rows in grouped.items():
                    partial_line_evidence.append((crop_entry, candidate, rows))
        if len(partial_dates) == 1:
            for crop_entry, candidate, rows in partial_line_evidence:
                if candidate not in partial_dates:
                    continue
                run.pending_mismatch_evidence.append(
                    {
                        "rows": rows,
                        "variants": crop_entry["line_variants"],
                        "line_box": crop_entry["line_box"],
                        "region_box": crop_entry["region_box"],
                        "tight": crop_entry["tight"],
                        "coordinate_space": "line",
                        "partial_month_day": True,
                    }
                )


def _confirm_pending_date_mismatches(run: DateCropRun) -> None:
    if run.secondary_ocr_backend == "paddle" and run.pending_mismatch_evidence:
        required = parse_date(run.required_text)
        mobile_dates = {
            parsed
            for item in run.pending_mismatch_evidence
            for row in item["rows"]
            if (parsed := parse_receipt_date(row.text, required)) is not None
            and parsed != required
        }
        server_evidence = []
        from .paddle_ocr import recognize_line

        for crop_entry in run.date_crop_entries:
            if crop_entry.get("audit_only"):
                continue
            tight = crop_entry["tight"]
            line_raw = crop_entry["line_raw"]
            line_box = crop_entry["line_box"]
            region_box = crop_entry["region_box"]
            line_variants = crop_entry["line_variants"]
            try:
                server_rows = recognize_line(line_raw, model_variant="server")
            except Exception:
                server_rows = []
            strict_rows = [
                row
                for row in server_rows
                if row.confidence >= 0.80
                and (parsed := parse_date(row.text)) in mobile_dates
                and parsed != required
            ]
            line_variants.append(
                {
                    "preprocessing": "日期行 Server 大模型复核不一致日期",
                    "ocr_texts": [row.text for row in server_rows],
                    "accepted_texts": [row.text for row in strict_rows],
                    "acceptance_note": (
                        "Mobile 区域识别 + Server 另一几何裁剪完整日期一致"
                        if strict_rows
                        else "未形成跨模型一致的不匹配日期"
                    ),
                }
            )
            for row in strict_rows:
                server_evidence.append((tight, row, line_box, region_box))
        corroborated = {
            parse_date(row.text)
            for tight, row, _, _ in server_evidence
            if any(
                not item["tight"] == tight
                and parse_receipt_date(mobile_row.text, required)
                == parse_date(row.text)
                and (
                    row.confidence >= 0.82
                    or (
                        item.get("coordinate_space") == "line"
                        and mobile_row.confidence >= 0.72
                    )
                )
                for item in run.pending_mismatch_evidence
                for mobile_row in item["rows"]
            )
        }
        for item in run.pending_mismatch_evidence:
            x, y, width, height = item["region_box"]
            accepted_mobile = []
            for row in item["rows"]:
                parsed = parse_receipt_date(row.text, required)
                if parsed not in corroborated:
                    continue
                accepted_mobile.append(row.text)
                if item.get("coordinate_space") == "line":
                    lx, ly, lw, lh = item["line_box"]
                    run.output.append(
                        type(row)(
                            text=row.text,
                            confidence=row.confidence,
                            x=x + (lx + row.x * lw) * width,
                            y=y + (ly + row.y * lh) * height,
                            width=row.width * lw * width,
                            height=row.height * lh * height,
                        )
                    )
                else:
                    run.output.append(
                        type(row)(
                            text=row.text,
                            confidence=row.confidence,
                            x=x + row.x * width,
                            y=y + row.y * height,
                            width=row.width * width,
                            height=row.height * height,
                        )
                    )
            for variant in item.get("variants", []):
                accepted = [
                    text
                    for text in variant.get("ocr_texts", [])
                    if parse_receipt_date(text, required) in corroborated
                ]
                if accepted:
                    variant["accepted_texts"] = accepted
                    variant["acceptance_note"] = "与 Server 另一几何裁剪完整日期一致"
        for tight, row, line_box, region_box in server_evidence:
            parsed = parse_date(row.text)
            if parsed not in corroborated:
                continue
            lx, ly, lw, lh = line_box
            x, y, width, height = region_box
            run.output.append(
                type(row)(
                    text=row.text,
                    confidence=row.confidence,
                    x=x + (lx + row.x * lw) * width,
                    y=y + (ly + row.y * lh) * height,
                    width=row.width * lw * width,
                    height=row.height * lh * height,
                )
            )
