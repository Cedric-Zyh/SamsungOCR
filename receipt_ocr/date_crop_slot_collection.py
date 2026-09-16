"""Prepare fixed date slots and collect per-model component readings."""

from __future__ import annotations
from .date_evidence import (
    _missing_month_day_component_prefilter_from_artifacts,
    _parse_date_slot_digit,
    _parse_date_slot_month_day,
    _parse_date_slot_year,
    _recognize_adaptive_day_slot_variants,
    _save_date_slot_views,
    _server_cross_geometry_strict_date_from_artifacts,
    _single_server_strict_truncated_day_candidate,
)
from .date_crop_state import DateCropRun, DateSlotProbe


def _prepare_slot_probe(run: DateCropRun, probe: DateSlotProbe) -> None:
    try:
        probe.slot_views = _save_date_slot_views(
            probe.tight_entry["line_raw"], run.temp_dir
        )
    except Exception:
        probe.slot_views = {}
    probe.year_view = probe.slot_views.get("year_full", {})
    probe.month_context_view = probe.slot_views.get("month_context", {})
    probe.day_context_view = probe.slot_views.get("day_context", {})
    probe.adaptive_day_view = probe.slot_views.get("day_digits_adaptive", {})
    probe.month_day_view = probe.slot_views.get("month_day", {})
    probe.month_digit_view = probe.slot_views.get("month_digits", {})
    probe.year_path = probe.year_view.get("processed")
    probe.month_day_path = probe.month_day_view.get("processed")
    probe.month_digit_path = probe.month_digit_view.get("processed")
    probe.slot_variants = []
    probe.safe_year_variants: list[dict] = []
    probe.safe_month_day_variants: list[dict] = []
    probe.server_component_variants: list[dict] = []
    probe.server_strict_candidate = _server_cross_geometry_strict_date_from_artifacts(
        run.artifacts
    )
    probe.truncated_day_candidate = _single_server_strict_truncated_day_candidate(
        run.artifacts
    )
    probe.adaptive_day_variants = (
        _recognize_adaptive_day_slot_variants(probe.adaptive_day_view)
        if probe.truncated_day_candidate is not None
        else []
    )
    probe.slot_variants.extend(probe.adaptive_day_variants)
    probe.missing_month_component_prefilter = (
        _missing_month_day_component_prefilter_from_artifacts(run.artifacts)
    )


def _collect_slot_variants(
    run: DateCropRun, probe: DateSlotProbe, recognize_line
) -> None:
    for preprocessing, safe_month_digit_path in (
        ("最大通道去彩色", probe.month_digit_path),
        (
            "最大通道去彩色并去横线",
            probe.month_digit_view.get("line_clean"),
        ),
    ):
        if safe_month_digit_path is None:
            continue
        for model_variant, model_label in (
            ("mobile", "Mobile"),
            ("server", "Server"),
        ):
            try:
                digit_rows = recognize_line(
                    safe_month_digit_path,
                    model_variant=model_variant,
                )
            except Exception:
                digit_rows = []
            digit_values = {
                parsed
                for row in digit_rows
                if (parsed := _parse_date_slot_digit(row.text, maximum=12)) is not None
            }
            probe.slot_variants.append(
                {
                    "slot": "月份数字窄槽",
                    "method": (f"{model_label} {preprocessing}整行识别"),
                    "model": model_variant,
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in digit_rows],
                    "parsed_components": [str(value) for value in sorted(digit_values)],
                }
            )
    if (
        probe.missing_month_component_prefilter is not None
        and run.ocr_backend == "vision"
        and run.secondary_ocr_backend == "paddle"
    ):
        for preprocessing, context_path in (
            (
                "最大通道去彩色",
                probe.month_context_view.get("processed"),
            ),
            (
                "最大通道去彩色并去横线",
                probe.month_context_view.get("line_clean"),
            ),
        ):
            if context_path is None:
                continue
            try:
                month_context_rows = recognize_line(
                    context_path,
                    model_variant="mobile",
                )
            except Exception:
                month_context_rows = []
            probe.slot_variants.append(
                {
                    "slot": "月份上下文槽位",
                    "method": (f"Mobile {preprocessing}整行识别"),
                    "model": "mobile",
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in month_context_rows],
                    "parsed_components": [],
                }
            )
    probe.month_day_by_model: dict[str, set[tuple[int, int]]] = {}
    for preprocessing, safe_month_day_path in (
        ("最大通道去彩色", probe.month_day_path),
        (
            "最大通道去彩色并去横线",
            probe.month_day_view.get("line_clean"),
        ),
    ):
        if safe_month_day_path is None:
            continue
        for model_variant, model_label in (
            ("mobile", "Mobile"),
            ("server", "Server"),
        ):
            try:
                slot_rows = recognize_line(
                    safe_month_day_path,
                    model_variant=model_variant,
                )
            except Exception:
                slot_rows = []
            values = {
                parsed
                for row in slot_rows
                if (parsed := _parse_date_slot_month_day(row.text)) is not None
            }
            if preprocessing == "最大通道去彩色":
                probe.month_day_by_model[model_variant] = values
            safe_variant = {
                "slot": "月日联合槽位",
                "method": (f"{model_label} {preprocessing}整行识别"),
                "model": model_variant,
                "preprocessing": preprocessing,
                "ocr_texts": [row.text for row in slot_rows],
                "parsed_components": [
                    f"{month:02d}-{day:02d}" for month, day in sorted(values)
                ],
            }
            probe.slot_variants.append(safe_variant)
            probe.safe_month_day_variants.append(safe_variant)
    for preprocessing, safe_year_path in (
        ("最大通道去彩色", probe.year_path),
        (
            "最大通道去彩色并去横线",
            probe.year_view.get("line_clean"),
        ),
    ):
        if safe_year_path is None:
            continue
        for model_variant, model_label in (
            ("mobile", "Mobile"),
            ("server", "Server"),
        ):
            try:
                safe_year_rows = recognize_line(
                    safe_year_path,
                    model_variant=model_variant,
                )
            except Exception:
                safe_year_rows = []
            safe_years = {
                parsed
                for row in safe_year_rows
                if (parsed := _parse_date_slot_year(row.text)) is not None
            }
            safe_variant = {
                "slot": "完整年份槽位",
                "method": (f"{model_label} {preprocessing}整行识别"),
                "model": model_variant,
                "preprocessing": preprocessing,
                "ocr_texts": [row.text for row in safe_year_rows],
                "parsed_components": [str(value) for value in sorted(safe_years)],
            }
            probe.slot_variants.append(safe_variant)
            probe.safe_year_variants.append(safe_variant)
    if probe.server_strict_candidate is not None:
        for slot_label, context_path in (
            (
                "Server双几何完整年份上下文槽位",
                probe.year_view.get("processed"),
            ),
            (
                "Server双几何月日上下文槽位",
                probe.month_context_view.get("processed"),
            ),
            (
                "Server双几何日上下文审计槽位",
                probe.day_context_view.get("processed"),
            ),
        ):
            if context_path is None:
                continue
            for model_variant, model_label in (
                ("mobile", "Mobile"),
                ("server", "Server"),
            ):
                try:
                    context_rows = recognize_line(
                        context_path,
                        model_variant=model_variant,
                    )
                except Exception:
                    context_rows = []
                context_variant = {
                    "slot": slot_label,
                    "method": (f"{model_label} 最大通道去彩色" "整行识别"),
                    "model": model_variant,
                    "preprocessing": "最大通道去彩色",
                    "ocr_texts": [row.text for row in context_rows],
                    "parsed_components": [],
                }
                probe.slot_variants.append(context_variant)
                probe.server_component_variants.append(context_variant)
    probe.white_day_component_variants: list[dict] = []
    if probe.white_day_conflict_prefilter is not None:
        for slot_label, preprocessing, context_path in (
            (
                "整行冲突完整年份槽位",
                "最大通道去彩色",
                probe.year_view.get("processed"),
            ),
            (
                "整行冲突白边日上下文槽位",
                "白边标准化",
                probe.day_context_view.get("white_processed"),
            ),
            (
                "整行冲突白边日上下文槽位",
                "裁后白边标准化",
                probe.day_context_view.get("crop_first_white_processed"),
            ),
        ):
            if context_path is None:
                continue
            for model_variant, model_label in (
                ("mobile", "Mobile"),
                ("server", "Server"),
            ):
                try:
                    context_rows = recognize_line(
                        context_path,
                        model_variant=model_variant,
                    )
                except Exception:
                    context_rows = []
                context_variant = {
                    "slot": slot_label,
                    "method": (f"{model_label} {preprocessing}" "整行识别"),
                    "model": model_variant,
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in context_rows],
                    "parsed_components": [],
                }
                probe.slot_variants.append(context_variant)
                probe.white_day_component_variants.append(context_variant)
