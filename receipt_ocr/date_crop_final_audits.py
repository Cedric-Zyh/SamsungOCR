"""Resolve bounded separator/year conflicts and audit original handwriting."""

from __future__ import annotations
from .parser import (
    estimate_date_confidence,
    find_receipt_date,
    parse_date,
    parse_receipt_date,
)
from .ocr_types import TextObservation
from .date_evidence import (
    _cross_year_nondestructive_consensus_from_artifacts,
    _day_slot_confirms_value,
    _missing_year_separator_consensus_from_artifacts,
    _recognize_day_slot_variants,
    _save_date_slot_views,
)
from .date_crop_state import DateCropRun


def _confirm_missing_year_separator(run: DateCropRun) -> None:
    # A missing printed ``年`` can leave a fully self-contained Mobile
    # reading such as ``20256月16日``.  Use it only when Server exposes
    # the same strict date and every other parsed date is the matching
    # two-digit day reduced to one digit.  The final gate is a second,
    # unit-excluding day crop: Mobile and Server must both read the
    # complete two-digit day from two conservative preprocessings.
    current_separator_date, _ = find_receipt_date(run.output, run.required_text)
    current_separator_confidence = estimate_date_confidence(
        run.output, run.required_text, current_separator_date
    )
    separator_consensus = (
        _missing_year_separator_consensus_from_artifacts(
            run.artifacts, run.required_text
        )
        if current_separator_confidence < 0.72
        else None
    )
    if separator_consensus is not None:
        candidate_date = separator_consensus["date"]
        tight_entry = next(
            (
                item
                for item in run.date_crop_entries
                if item.get("crop_key") == "tight" and not item.get("audit_only")
            ),
            None,
        )
        tight_artifact = next(
            (item for item in run.artifacts if item.get("variant") == "紧凑区域"), None
        )
        try:
            inner_slot_views = (
                _save_date_slot_views(tight_entry["line_raw"], run.temp_dir)
                if tight_entry is not None
                else {}
            )
        except Exception:
            inner_slot_views = {}
        inner_day_view = inner_slot_views.get("day_digits_inner", {})
        inner_day_variants = _recognize_day_slot_variants(inner_day_view)
        if _day_slot_confirms_value(inner_day_variants, candidate_date.day):
            if tight_entry is not None:
                x, y, width, height = tight_entry["region_box"]
                lx, ly, lw, lh = tight_entry["line_box"]
                for index in range(2):
                    run.output.append(
                        TextObservation(
                            text=(
                                f"{candidate_date.year}年"
                                f"{candidate_date.month}月"
                                f"{candidate_date.day}日"
                            ),
                            confidence=0.84,
                            x=x + (lx + (0.58 + index * 0.01) * lw) * width,
                            y=y + ly * height,
                            width=0.20 * lw * width,
                            height=lh * height,
                        )
                    )
            for artifact in run.artifacts:
                if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                    artifact.update(
                        {
                            "date_missing_year_separator_candidate": (
                                candidate_date.isoformat()
                            ),
                            "date_missing_year_separator_note": (
                                "Mobile 自包含四位年份但漏印刷“年”，"
                                "Server 字面完整日期一致；其他日期至多为"
                                "两位日漏一位，去单位日数字窄槽经双模型、"
                                "双预处理确认"
                            ),
                        }
                    )
            if tight_artifact is not None and inner_day_view:
                prefix = run.artifact_url_prefix.rstrip("/")
                tight_artifact.update(
                    {
                        "date_slot_day_inner_original_url": (
                            f"{prefix}/date/" f"{inner_day_view['original'].name}"
                        ),
                        "date_slot_day_inner_processed_url": (
                            f"{prefix}/date/" f"{inner_day_view['processed'].name}"
                        ),
                        "date_slot_day_inner_line_clean_url": (
                            f"{prefix}/date/" f"{inner_day_view['line_clean'].name}"
                        ),
                        "date_slot_day_inner_ocr_variants": (inner_day_variants),
                        "date_slot_day_inner_candidate": str(candidate_date.day),
                        "date_slot_day_inner_reliable": True,
                        "date_slot_day_inner_acceptance_note": (
                            "去除右侧印刷“日”后，Mobile/Server 在"
                            "最大通道去彩色及去横线两种图上均只读到"
                            "完整两位日；要求日期未用于补年、月或日"
                        ),
                        "date_missing_year_separator_support": (
                            {
                                "candidate": candidate_date.isoformat(),
                                "mobile": separator_consensus["mobile"],
                                "server": separator_consensus["server"],
                                "other_dates": separator_consensus["other_dates"],
                            }
                        ),
                    }
                )


def _confirm_nondestructive_cross_year(run: DateCropRun) -> None:
    # Resolve one tightly bounded cross-year disagreement without
    # majority-voting destructive variants. Mobile must read the
    # same literal date from the original and color-cleaned line in
    # both geometries; Server must independently agree in every wide
    # audit cell that ran. Tight Server cells must expose the printed required
    # year, making the discarded conflict explicit and auditable.
    current_nondestructive_date, _ = find_receipt_date(run.output, run.required_text)
    current_nondestructive_confidence = estimate_date_confidence(
        run.output, run.required_text, current_nondestructive_date
    )
    nondestructive_cross_year = (
        _cross_year_nondestructive_consensus_from_artifacts(
            run.artifacts, run.required_text
        )
        if current_nondestructive_confidence < 0.72
        else None
    )
    if nondestructive_cross_year is not None:
        candidate_date = nondestructive_cross_year["date"]
        tight_entry = next(
            (
                item
                for item in run.date_crop_entries
                if item.get("crop_key") == "tight" and not item.get("audit_only")
            ),
            None,
        )
        if tight_entry is not None:
            x, y, width, height = tight_entry["region_box"]
            lx, ly, lw, lh = tight_entry["line_box"]
            for index in range(2):
                run.output.append(
                    TextObservation(
                        text=(
                            f"{candidate_date.year}年"
                            f"{candidate_date.month}月"
                            f"{candidate_date.day}日"
                        ),
                        confidence=0.88,
                        x=x + (lx + index * 0.01 * lw) * width,
                        y=y + ly * height,
                        width=lw * width,
                        height=lh * height,
                    )
                )
        for artifact in run.artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_cross_year_nondestructive_candidate": (
                            candidate_date.isoformat()
                        ),
                        "date_cross_year_nondestructive_note": (
                            "Mobile 在紧裁/宽裁的原始与去章色八单元"
                            "只读到该完整日期；Server 宽裁已运行的"
                            "独立复核路径一致。紧裁 Server 的要求年份冲突和"
                            "破坏性预处理干扰均保留供人工审计"
                        ),
                        "date_cross_year_nondestructive_support": (
                            nondestructive_cross_year["support"]
                        ),
                    }
                )


def _audit_original_color_handwriting(run: DateCropRun) -> None:
    # A business-impossible candidate (for example a date earlier
    # than document creation) must not prevent one final audit of the
    # untouched color handwriting.  Run this only when every existing
    # parseable date is impossible by that independent business rule,
    # or when there is no parseable date at all.  Three Vision language
    # configurations must agree on one complete date from the same
    # original-color white canvas.  This remains 20% review evidence;
    # it never resolves a plausible conflict or becomes reliable.
    if run.ocr_backend == "vision" and run.secondary_ocr_backend == "paddle":
        required_for_white = parse_date(run.required_text)
        creation_for_white = parse_date(run.creation_text)
        existing_dates = {
            parsed
            for row in run.output
            if (
                parsed := (
                    parse_date(row.text)
                    or (
                        parse_receipt_date(row.text, required_for_white)
                        if required_for_white is not None
                        else None
                    )
                )
            )
            is not None
        }
        viable_existing_dates = {
            value
            for value in existing_dates
            if creation_for_white is None or value >= creation_for_white
        }
        if not viable_existing_dates:
            tight_entry = next(
                (
                    item
                    for item in run.date_crop_entries
                    if item.get("crop_key") == "tight" and not item.get("audit_only")
                ),
                None,
            )
            tight_artifact = next(
                (item for item in run.artifacts if item.get("variant") == "紧凑区域"),
                None,
            )
            line_white_path = (
                tight_entry.get("line_white_standardized")
                if tight_entry is not None
                else None
            )
            if (
                tight_entry is not None
                and tight_artifact is not None
                and line_white_path is not None
                and line_white_path.is_file()
            ):
                (
                    line_white_variants,
                    line_white_dates,
                ) = run.services.recognize_date_line_vision_consensus(line_white_path)
                tight_entry["line_variants"].extend(line_white_variants)
                if len(line_white_dates) == 1:
                    line_white_candidate = next(iter(line_white_dates))
                    if (
                        creation_for_white is not None
                        and line_white_candidate < creation_for_white
                    ):
                        tight_artifact.update(
                            {
                                "date_line_white_rejected_candidate": (
                                    line_white_candidate.isoformat()
                                ),
                                "date_line_white_acceptance_note": (
                                    "Vision 白底多配置候选早于制单日期，"
                                    "仅保留原始证据，未用于日期判定"
                                ),
                            }
                        )
                    else:
                        x, y, width, height = tight_entry["region_box"]
                        lx, ly, lw, lh = tight_entry["line_box"]
                        run.output.append(
                            TextObservation(
                                text=(
                                    f"{line_white_candidate.year}年"
                                    f"{line_white_candidate.month}月"
                                    f"{line_white_candidate.day}日"
                                ),
                                confidence=0.20,
                                x=x + lx * width,
                                y=y + ly * height,
                                width=lw * width,
                                height=lh * height,
                            )
                        )
                        tight_artifact.update(
                            {
                                "date_line_white_candidate": (
                                    line_white_candidate.isoformat()
                                ),
                                "date_line_white_acceptance_note": (
                                    "原色白底日期行在三种 Vision 语言"
                                    "配置下输出同一完整日期；单一系统"
                                    "模型证据仅供人工复核，置信度封顶 20%"
                                ),
                            }
                        )
