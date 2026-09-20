"""Select consistent slot dates and bound last-resort review suggestions."""

from __future__ import annotations
from datetime import date
from .ocr_types import TextObservation
from .date_evidence import _parse_date_slot_year
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


def _recover_unparsed_slot_date(run: DateCropRun, probe: DateSlotProbe) -> None:
    """Publish the slot candidate both Paddle models agreed on.

    The fixed-template row is one physical line, so the reading is capped at
    low confidence and can only populate the pending-review UI.
    """
    probe.slot_component_candidate = probe.accepted_slot_date
    if probe.accepted_slot_date is not None:
        x, y, width, height = probe.tight_entry["region_box"]
        lx, ly, lw, lh = probe.tight_entry["line_box"]
        for observation_index in range(1):
            run.output.append(
                TextObservation(
                    text=(
                        f"{probe.accepted_slot_date.year}年"
                        f"{probe.accepted_slot_date.month}月"
                        f"{probe.accepted_slot_date.day}日"
                    ),
                    confidence=0.25,
                    x=(x + (lx + (0.30 + 0.01 * observation_index) * lw) * width),
                    y=y + ly * height,
                    width=0.68 * lw * width,
                    height=lh * height,
                )
            )
