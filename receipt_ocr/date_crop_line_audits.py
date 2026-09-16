"""Collect Server, standardized-white and Otsu date-line audit evidence."""

from __future__ import annotations
from datetime import date
from .parser import find_receipt_date, parse_date, parse_receipt_date
from .date_evidence import (
    _parse_compact_full_date_audit_candidate,
    _parse_server_audit_candidate,
    _unique_server_mobile_otsu_candidate,
)
from .date_crop_state import DateCropRun


def _audit_server_date_lines(
    run: DateCropRun,
    required: date,
    recognize_line,
    server_strict_audit_texts: list[str],
) -> None:
    for crop_entry in run.date_crop_entries:
        if crop_entry.get("audit_only"):
            continue
        try:
            audit_rows = recognize_line(crop_entry["line_raw"], model_variant="server")
        except Exception:
            audit_rows = []
        partial_rows = [
            row
            for row in audit_rows
            if parse_date(row.text) is None
            and parse_receipt_date(row.text, required) is not None
        ]
        strict_other_year_rows = [
            row
            for row in audit_rows
            if (parsed := parse_date(row.text)) is not None
            and parsed.year != required.year
        ]
        server_strict_audit_texts.extend(
            row.text for row in audit_rows if parse_date(row.text) is not None
        )
        crop_entry["line_variants"].append(
            {
                "preprocessing": "日期行 Server 大模型低置信度候选",
                "ocr_texts": [row.text for row in audit_rows],
                "accepted_texts": [
                    row.text for row in partial_rows + strict_other_year_rows
                ],
                "acceptance_note": (
                    "仅作为人工复核候选；跨年份完整日期还需两种几何裁剪一致，"
                    "不参与自动放行"
                ),
            }
        )
        lx, ly, lw, lh = crop_entry["line_box"]
        x, y, width, height = crop_entry["region_box"]
        for row in partial_rows:
            run.output.append(
                type(row)(
                    text=row.text,
                    confidence=min(0.45, row.confidence),
                    x=x + (lx + row.x * lw) * width,
                    y=y + (ly + row.y * lh) * height,
                    width=row.width * lw * width,
                    height=row.height * lh * height,
                )
            )
        for row in strict_other_year_rows:
            run.output.append(
                type(row)(
                    text=row.text,
                    confidence=min(0.35, row.confidence),
                    x=x + (lx + row.x * lw) * width,
                    y=y + (ly + row.y * lh) * height,
                    width=row.width * lw * width,
                    height=row.height * lh * height,
                )
            )


def _expose_repeated_strict_audit(
    run: DateCropRun, recognize_line, server_strict_audit_texts: list[str]
) -> None:
    # A single Server reading is only 44% correct on the
    # reviewed 301-receipt corpus.  Expose a same-year value
    # only when Mobile independently reads the exact same
    # strict date from an Otsu view and all previously parsed
    # strict line dates agree.  The evidence still comes from
    # one physical line, so confidence is capped at 45% and
    # the receipt remains in manual review.
    if find_receipt_date(run.output, run.required_text)[0] is None:
        existing_strict_texts = [
            text
            for crop_entry in run.date_crop_entries
            for variant in crop_entry.get("line_variants", [])
            for text in variant.get("ocr_texts", [])
        ]
        for crop_entry in run.date_crop_entries:
            if crop_entry.get("audit_only") or crop_entry.get("crop_key") not in {
                "tight",
                "wide",
            }:
                continue
            otsu_path = crop_entry.get("line_otsu_upscaled")
            if otsu_path is None or not otsu_path.is_file():
                continue
            try:
                otsu_rows = recognize_line(otsu_path, model_variant="mobile")
            except Exception:
                otsu_rows = []
            candidate = _unique_server_mobile_otsu_candidate(
                server_strict_audit_texts,
                [row.text for row in otsu_rows],
                existing_strict_texts,
            )
            accepted_otsu = (
                [row for row in otsu_rows if parse_date(row.text) == candidate]
                if candidate is not None
                else []
            )
            crop_entry["line_variants"].append(
                {
                    "preprocessing": ("日期行 Otsu 三倍放大 Mobile/Server " "人工候选"),
                    "ocr_texts": [row.text for row in otsu_rows],
                    "accepted_texts": [row.text for row in accepted_otsu],
                    "acceptance_note": (
                        "唯一 Server 完整日期与 Mobile Otsu "
                        "严格同日；同一物理行证据，置信度封顶 45%，"
                        "仅供人工复核"
                        if accepted_otsu
                        else "未形成唯一的跨模型严格同日，不参与判定"
                    ),
                }
            )
            if not accepted_otsu:
                continue
            row = max(
                accepted_otsu,
                key=lambda item: item.confidence,
            )
            lx, ly, lw, lh = crop_entry["line_box"]
            x, y, width, height = crop_entry["region_box"]
            run.output.append(
                type(row)(
                    text=row.text,
                    # Keep the individual observation below the
                    # repeated-audit consensus ceiling as well as
                    # applying the 45% result-level cap above.
                    # This prevents several transforms of the same
                    # physical line from reaching 72% internally.
                    confidence=min(0.25, row.confidence),
                    x=x + (lx + row.x * lw) * width,
                    y=y + (ly + row.y * lh) * height,
                    width=row.width * lw * width,
                    height=row.height * lh * height,
                )
            )
            break


def _audit_white_date_lines(run: DateCropRun, required: date, recognize_line) -> None:
    # A three-times enlarged derivative of the already
    # line-cleaned image helps the Server recognizer on a few
    # thin handwritten dates. It remains a single-model audit
    # source: cap every observation at 0.25 so even matching
    # tight/wide results cannot reach the reliable threshold.
    for crop_entry in run.date_crop_entries:
        if crop_entry.get("audit_only") or crop_entry.get("crop_key") not in {
            "tight",
            "wide",
        }:
            continue
        enhanced_path = crop_entry.get("line_table_clean_upscaled")
        if enhanced_path is None or not enhanced_path.is_file():
            continue
        try:
            enhanced_rows = recognize_line(enhanced_path, model_variant="server")
        except Exception:
            enhanced_rows = []
        accepted_enhanced = [
            row
            for row in enhanced_rows
            if _parse_server_audit_candidate(row.text, required) is not None
        ]
        crop_entry["line_variants"].append(
            {
                "preprocessing": ("日期行去表格线三倍放大 Server 人工候选"),
                "ocr_texts": [row.text for row in enhanced_rows],
                "accepted_texts": [row.text for row in accepted_enhanced],
                "acceptance_note": ("单一大模型放大图证据，仅供人工复核"),
            }
        )
        lx, ly, lw, lh = crop_entry["line_box"]
        x, y, width, height = crop_entry["region_box"]
        for row in accepted_enhanced:
            run.output.append(
                type(row)(
                    text=row.text,
                    confidence=min(0.25, row.confidence),
                    x=x + (lx + row.x * lw) * width,
                    y=y + (ly + row.y * lh) * height,
                    width=row.width * lw * width,
                    height=row.height * lh * height,
                )
            )


def _audit_otsu_date_lines(run: DateCropRun, recognize_line) -> None:
    # OCR sometimes preserves every date digit while dropping
    # only the printed ``月`` separator, for example
    # ``2025年1218日``.  A grayscale autocontrast enlargement
    # exposes that complete eight-digit structure on thin
    # handwriting.  Accept only a self-contained, valid
    # YYYYMMDD value ending in ``日``; never repair it from the
    # required date.  Both Mobile and Server readings remain
    # low-confidence audit evidence and cannot auto-pass.
    for crop_entry in run.date_crop_entries:
        if crop_entry.get("audit_only") or crop_entry.get("crop_key") not in {
            "tight",
            "wide",
        }:
            continue
        enhanced_path = crop_entry.get("line_autocontrast_upscaled")
        if enhanced_path is None or not enhanced_path.is_file():
            continue
        for model_variant, model_label in (
            ("mobile", "Mobile"),
            ("server", "Server"),
        ):
            try:
                enhanced_rows = recognize_line(
                    enhanced_path, model_variant=model_variant
                )
            except Exception:
                enhanced_rows = []
            accepted_enhanced = [
                (row, parsed)
                for row in enhanced_rows
                if (parsed := _parse_compact_full_date_audit_candidate(row.text))
                is not None
            ]
            crop_entry["line_variants"].append(
                {
                    "preprocessing": (
                        "日期行灰度自动对比三倍放大 " f"{model_label} 人工候选"
                    ),
                    "ocr_texts": [row.text for row in enhanced_rows],
                    "accepted_texts": [row.text for row, _ in accepted_enhanced],
                    "acceptance_note": (
                        "完整8位合法日期，仅供人工复核；" "单一增强路径不自动放行"
                    ),
                }
            )
            lx, ly, lw, lh = crop_entry["line_box"]
            x, y, width, height = crop_entry["region_box"]
            for row, parsed in accepted_enhanced:
                run.output.append(
                    type(row)(
                        text=(f"{parsed.year}年{parsed.month}月" f"{parsed.day}日"),
                        confidence=min(0.25, row.confidence),
                        x=x + (lx + row.x * lw) * width,
                        y=y + (ly + row.y * lh) * height,
                        width=row.width * lw * width,
                        height=row.height * lh * height,
                    )
                )
