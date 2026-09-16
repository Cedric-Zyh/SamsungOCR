"""Select consistent slot dates and bound last-resort review suggestions."""

from __future__ import annotations
from datetime import date
from .parser import parse_date, parse_receipt_date
from .ocr_types import TextObservation
from .date_evidence import (
    _adaptive_day_slot_confirms_value,
    _cross_model_server_strict_component_date,
    _cross_model_slot_required_date,
    _is_date_audit_only_preprocessing,
    _parse_date_slot_month_day,
    _parse_date_slot_year,
    _same_geometry_missing_month_consensus_from_artifacts,
    _white_day_conflict_audit_candidate,
)
from .date_crop_state import DateCropRun, DateSlotProbe


def _select_slot_date(run: DateCropRun, probe: DateSlotProbe) -> None:
    common_month_days = probe.month_day_by_model.get(
        "mobile", set()
    ) & probe.month_day_by_model.get("server", set())
    common_years: set[int] = set()
    if probe.year_path is not None and len(common_month_days) == 1:
        from .paddle_ocr import (
            recognize_text as paddle_recognize_text,
        )

        year_by_model: dict[str, set[int]] = {}
        for model_variant, model_label in (
            ("mobile", "Mobile"),
            ("server", "Server"),
        ):
            try:
                year_rows = paddle_recognize_text(
                    probe.year_path,
                    model_variant=model_variant,
                    min_text_height=0.001,
                )
            except Exception:
                year_rows = []
            years = {
                parsed
                for row in year_rows
                if (parsed := _parse_date_slot_year(row.text)) is not None
            }
            year_by_model[model_variant] = years
            probe.slot_variants.append(
                {
                    "slot": "完整年份槽位",
                    "method": f"{model_label} 检测识别",
                    "ocr_texts": [row.text for row in year_rows],
                    "parsed_components": [str(value) for value in sorted(years)],
                }
            )
        common_years = year_by_model.get("mobile", set()) & year_by_model.get(
            "server", set()
        )
    probe.accepted_slot_date = None
    probe.slot_candidate_source = ""
    if len(common_years) == len(common_month_days) == 1:
        year = next(iter(common_years))
        month, day = next(iter(common_month_days))
        try:
            probe.accepted_slot_date = date(year, month, day)
            probe.slot_candidate_source = "Paddle Mobile/Server 年月日槽位一致"
        except ValueError:
            probe.accepted_slot_date = None
    existing_slot_texts = [row.text for row in run.output] + [
        str(text)
        for entry in run.date_crop_entries
        for variant in entry.get("line_variants", [])
        if not _is_date_audit_only_preprocessing(str(variant.get("preprocessing", "")))
        for text in variant.get("ocr_texts", []) or []
    ]
    server_component_existing_texts = [
        str(text)
        for entry in run.date_crop_entries
        for variant in entry.get("line_variants", [])
        if not _is_date_audit_only_preprocessing(str(variant.get("preprocessing", "")))
        for text in variant.get("ocr_texts", []) or []
    ]
    probe.reliable_slot_date = None
    probe.server_component_date = None
    probe.white_day_audit_date = None
    probe.same_geometry_missing_month_date = None
    if run.ocr_backend == "vision" and run.secondary_ocr_backend == "paddle":
        probe.reliable_slot_date = _cross_model_slot_required_date(
            probe.safe_year_variants,
            probe.safe_month_day_variants,
            run.required_text,
            existing_slot_texts,
        )
        probe.server_component_date = _cross_model_server_strict_component_date(
            probe.server_strict_candidate,
            probe.server_component_variants,
            server_component_existing_texts,
        )
        if probe.reliable_slot_date is None and probe.server_component_date is not None:
            probe.reliable_slot_date = probe.server_component_date
        if (
            probe.reliable_slot_date is None
            and probe.truncated_day_candidate is not None
            and _adaptive_day_slot_confirms_value(
                probe.adaptive_day_variants,
                probe.truncated_day_candidate.day,
            )
        ):
            probe.reliable_slot_date = probe.truncated_day_candidate
        probe.white_day_audit_date = _white_day_conflict_audit_candidate(
            probe.white_day_conflict_prefilter,
            probe.white_day_component_variants,
        )
        same_geometry_missing_month = (
            _same_geometry_missing_month_consensus_from_artifacts(
                run.artifacts,
                run.required_text,
                probe.slot_variants,
            )
        )
        if same_geometry_missing_month is not None:
            probe.same_geometry_missing_month_date = same_geometry_missing_month["date"]
            if probe.reliable_slot_date is None:
                probe.reliable_slot_date = probe.same_geometry_missing_month_date
    if probe.reliable_slot_date is not None:
        probe.accepted_slot_date = probe.reliable_slot_date
        probe.slot_candidate_source = (
            "Server 唯一完整日期 + Mobile 紧宽区域"
            "同位漏字 + Paddle 双模型自适应日位一致"
            if (probe.truncated_day_candidate == probe.reliable_slot_date)
            else (
                "Server 双几何完整日期 + Paddle 双模型" "固定年月日上下文槽位一致"
                if probe.server_component_date is not None
                else (
                    "Server 完整日期 + Mobile 漏月份日期 + "
                    "Paddle 双模型月份窄槽三单元一致"
                    if probe.same_geometry_missing_month_date is not None
                    else "Paddle Mobile/Server 双预处理槽位严格一致"
                )
            )
        )
    elif probe.white_day_audit_date is not None:
        probe.accepted_slot_date = probe.white_day_audit_date
        probe.slot_candidate_source = (
            "Mobile 双几何日期 + Paddle 双模型白边日" "上下文审计建议"
        )


def _recover_unparsed_slot_date(
    run: DateCropRun, probe: DateSlotProbe, recognize_line
) -> None:
    # Last-resort macOS suggestion for rows where every
    # established path has *no parseable date evidence*.
    # Paddle Mobile and Server must agree on an explicit
    # four-digit year from the standardized white view.
    # Vision supplies a self-contained month/day from a
    # separately preprocessed crop-first white view.
    # This is deliberately one Vision path, so the
    # resulting observation stays at 22% confidence and
    # can only populate the pending-review UI.
    required_for_slot = parse_date(run.required_text)
    has_parseable_date_evidence = any(
        parse_date(row.text) is not None
        or (
            required_for_slot is not None
            and parse_receipt_date(row.text, required_for_slot) is not None
        )
        for row in run.output
    )
    if (
        probe.accepted_slot_date is None
        and run.ocr_backend == "vision"
        and run.secondary_ocr_backend == "paddle"
        and not has_parseable_date_evidence
    ):
        year_white_path = probe.year_view.get("white_processed")
        vision_month_day_path = probe.month_day_view.get("crop_first_white_processed")
        fallback_year_by_model: dict[str, set[int]] = {}
        if year_white_path is not None:
            for model_variant, model_label in (
                ("mobile", "Mobile"),
                ("server", "Server"),
            ):
                try:
                    fallback_year_rows = recognize_line(
                        year_white_path,
                        model_variant=model_variant,
                    )
                except Exception:
                    fallback_year_rows = []
                fallback_years = {
                    parsed
                    for row in fallback_year_rows
                    if (parsed := _parse_date_slot_year(row.text)) is not None
                }
                fallback_year_by_model[model_variant] = fallback_years
                probe.slot_variants.append(
                    {
                        "slot": "完整年份白底槽位",
                        "method": (f"{model_label} 整行识别"),
                        "ocr_texts": [row.text for row in fallback_year_rows],
                        "parsed_components": [
                            str(value) for value in sorted(fallback_years)
                        ],
                    }
                )
        fallback_common_years = fallback_year_by_model.get(
            "mobile", set()
        ) & fallback_year_by_model.get("server", set())
        vision_month_days: set[tuple[int, int]] = set()
        if vision_month_day_path is not None:
            vision_slot_rows = run.services.recognize_date_slot_with_vision(
                vision_month_day_path
            )
            vision_month_days = {
                parsed
                for row in vision_slot_rows
                if (parsed := _parse_date_slot_month_day(row.text)) is not None
            }
            probe.slot_variants.append(
                {
                    "slot": "月日裁后去章色白底槽位",
                    "method": "macOS Vision 精确识别",
                    "ocr_texts": [row.text for row in vision_slot_rows],
                    "parsed_components": [
                        f"{month:02d}-{day:02d}"
                        for month, day in sorted(vision_month_days)
                    ],
                }
            )
        if len(fallback_common_years) == 1 and len(vision_month_days) == 1:
            year = next(iter(fallback_common_years))
            month, day = next(iter(vision_month_days))
            try:
                probe.accepted_slot_date = date(year, month, day)
                probe.slot_candidate_source = "Paddle 双模型年份 + Vision 单路径月日"
            except ValueError:
                probe.accepted_slot_date = None
    probe.slot_component_candidate = probe.accepted_slot_date
    if probe.accepted_slot_date is not None:
        x, y, width, height = probe.tight_entry["region_box"]
        lx, ly, lw, lh = probe.tight_entry["line_box"]
        reliable_slot = probe.reliable_slot_date is not None
        observation_count = 2 if reliable_slot else 1
        for observation_index in range(observation_count):
            run.output.append(
                TextObservation(
                    text=(
                        f"{probe.accepted_slot_date.year}年"
                        f"{probe.accepted_slot_date.month}月"
                        f"{probe.accepted_slot_date.day}日"
                    ),
                    confidence=(
                        0.86
                        if probe.server_component_date
                        else (
                            0.84
                            if reliable_slot
                            else (
                                0.45
                                if probe.white_day_audit_date is not None
                                else (
                                    0.22
                                    if "Vision 单路径" in probe.slot_candidate_source
                                    else 0.25
                                )
                            )
                        )
                    ),
                    x=(x + (lx + (0.30 + 0.01 * observation_index) * lw) * width),
                    y=y + ly * height,
                    width=0.68 * lw * width,
                    height=lh * height,
                )
            )
