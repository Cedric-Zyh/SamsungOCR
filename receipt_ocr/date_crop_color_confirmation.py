"""Corroborate color-suppressed dates across models and crop geometries."""

from __future__ import annotations
from datetime import date
from .parser import parse_date
from .date_evidence import (
    _conflicting_receipt_dates,
    _cross_model_max_channel_mismatch_date,
    _cross_model_max_channel_required_with_truncated_conflict,
    _day_slot_confirms_value,
    _max_channel_truncated_mismatch_candidate,
    _parse_compact_full_date_audit_candidate,
    _recognize_day_slot_variants,
    _save_date_slot_views,
)
from .date_crop_state import DateCropRun


def _confirm_color_suppressed_dates(run: DateCropRun) -> None:
    # Colored stamp strokes can obscure an otherwise complete black
    # handwritten date.  The RGB per-pixel maximum suppresses red or
    # blue ink while preserving strokes that are dark in all three
    # channels.  Promote this derivative only with a strict complete
    # date from Mobile and Server on *different* tight/wide crops,
    # confidence >= 0.80 on both, and no other parseable date in the
    # established or new line evidence.  Thus the preprocessing adds
    # evidence but cannot turn one model/crop into a verdict.
    if run.secondary_ocr_backend == "paddle":
        required = parse_date(run.required_text)
        needs_max_channel_confirmation = bool(
            required is not None
            and not any(
                row.confidence >= 0.72 and parse_date(row.text) == required
                for row in run.output
            )
        )
        if needs_max_channel_confirmation:
            from .paddle_ocr import recognize_line

            enhanced_evidence = []
            enhanced_mismatch_evidence = []
            enhanced_all_rows = []
            for crop_entry in run.date_crop_entries:
                if crop_entry.get("audit_only") or crop_entry.get("crop_key") not in {
                    "tight",
                    "wide",
                }:
                    continue
                enhanced_path = crop_entry.get("line_max_channel_upscaled")
                if enhanced_path is None or not enhanced_path.is_file():
                    continue
                for model_variant, model_label in (
                    ("mobile", "Mobile"),
                    ("server", "Server"),
                ):
                    try:
                        enhanced_rows = recognize_line(
                            enhanced_path,
                            model_variant=model_variant,
                        )
                    except Exception:
                        enhanced_rows = []
                    enhanced_all_rows.extend(enhanced_rows)
                    accepted_pairs = []
                    for row in enhanced_rows:
                        parsed = parse_date(row.text)
                        compact = False
                        # Mobile occasionally preserves all eight
                        # date digits and the terminal ``日`` while
                        # dropping only the printed ``月`` separator
                        # (for example ``2025年1218日``).  This value
                        # is self-contained: no component comes from
                        # ``required``.  It may join the existing
                        # cross-model/cross-geometry route, but only
                        # Server's strict date from the other crop can
                        # confirm it below.
                        if parsed is None and model_variant == "mobile":
                            parsed = _parse_compact_full_date_audit_candidate(row.text)
                            compact = parsed is not None
                        if row.confidence < 0.80 or parsed != required:
                            continue
                        normalized = row
                        if compact:
                            normalized = type(row)(
                                text=(
                                    f"{parsed.year}年{parsed.month}月" f"{parsed.day}日"
                                ),
                                confidence=row.confidence,
                                x=row.x,
                                y=row.y,
                                width=row.width,
                                height=row.height,
                            )
                        accepted_pairs.append(
                            {
                                "raw_text": row.text,
                                "row": normalized,
                                "compact": compact,
                            }
                        )
                    strict_rows = [pair["row"] for pair in accepted_pairs]
                    variant = {
                        "preprocessing": (
                            "日期行最大通道去彩色三倍放大 " f"{model_label} 跨几何复核"
                        ),
                        "ocr_texts": [row.text for row in enhanced_rows],
                        "accepted_texts": [],
                        "acceptance_note": ("等待另一模型、另一几何裁剪确认"),
                    }
                    crop_entry["line_variants"].append(variant)
                    mismatch_by_date: dict[date, list] = {}
                    for row in enhanced_rows:
                        strict_date = parse_date(row.text)
                        if (
                            row.confidence >= 0.80
                            and strict_date is not None
                            and strict_date != required
                        ):
                            mismatch_by_date.setdefault(strict_date, []).append(row)
                    for mismatch_date, mismatch_rows in mismatch_by_date.items():
                        enhanced_mismatch_evidence.append(
                            {
                                "model": model_variant,
                                "tight": crop_entry["tight"],
                                "date": mismatch_date,
                                "rows": mismatch_rows,
                                "raw_texts": [row.text for row in mismatch_rows],
                                "entry": crop_entry,
                                "variant": variant,
                            }
                        )
                    if strict_rows:
                        enhanced_evidence.append(
                            {
                                "model": model_variant,
                                "tight": crop_entry["tight"],
                                "rows": strict_rows,
                                "raw_texts": [
                                    pair["raw_text"] for pair in accepted_pairs
                                ],
                                "compact": any(
                                    pair["compact"] for pair in accepted_pairs
                                ),
                                "entry": crop_entry,
                                "variant": variant,
                            }
                        )
            established_rows = list(run.output) + [
                row
                for crop_entry in run.date_crop_entries
                for row in crop_entry.get("line_rows") or []
            ]
            conflicts = _conflicting_receipt_dates(
                established_rows + enhanced_all_rows, required
            )
            confirming_evidence = None
            if not conflicts:
                for mobile_item in enhanced_evidence:
                    if mobile_item["model"] != "mobile":
                        continue
                    server_item = next(
                        (
                            server_item
                            for server_item in enhanced_evidence
                            if server_item["model"] == "server"
                            and server_item["tight"] != mobile_item["tight"]
                        ),
                        None,
                    )
                    if server_item is not None:
                        confirming_evidence = [mobile_item, server_item]
                        break
            truncated_conflict_consensus = False
            if confirming_evidence is None and conflicts:
                confirming_evidence = (
                    _cross_model_max_channel_required_with_truncated_conflict(
                        enhanced_evidence, conflicts, required
                    )
                )
                truncated_conflict_consensus = bool(confirming_evidence)
            if confirming_evidence is not None:
                for item in confirming_evidence:
                    crop_entry = item["entry"]
                    item["variant"]["accepted_texts"] = item["raw_texts"]
                    item["variant"]["acceptance_note"] = (
                        (
                            "3 个模型×几何单元格读到同一完整"
                            "日期；唯一其他值为两位日的单字截断"
                        )
                        if truncated_conflict_consensus
                        else (
                            (
                                "Mobile 自包含8位日期与 Server 严格日期"
                                "在紧/宽不同裁剪上同日"
                            )
                            if item.get("compact")
                            else (
                                "Mobile 与 Server 在紧/宽不同裁剪上读到" "同一完整日期"
                            )
                        )
                    )
                    lx, ly, lw, lh = crop_entry["line_box"]
                    x, y, width, height = crop_entry["region_box"]
                    for row in item["rows"]:
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
                if truncated_conflict_consensus:
                    for artifact in run.artifacts:
                        if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                            artifact.update(
                                {
                                    "date_max_channel_consensus_candidate": (
                                        required.isoformat()
                                    ),
                                    "date_max_channel_consensus_note": (
                                        "Mobile/Server 与紧/宽几何中至少"
                                        " 3 个单元格完整一致；唯一干扰"
                                        "是两位日的单字截断"
                                    ),
                                }
                            )
            else:
                note = (
                    "存在其他可解析日期，不参与自动判定"
                    if conflicts
                    else "未形成跨模型、跨几何一致日期"
                )
                for item in enhanced_evidence:
                    item["variant"]["acceptance_note"] = note
            mismatch_confirmation = _cross_model_max_channel_mismatch_date(
                enhanced_mismatch_evidence,
                established_rows + enhanced_all_rows,
                required,
            )
            truncated_mismatch_confirmation = None
            if mismatch_confirmation is None and run.ocr_backend == "vision":
                truncated_candidate = _max_channel_truncated_mismatch_candidate(
                    enhanced_mismatch_evidence,
                    established_rows + enhanced_all_rows,
                    required,
                )
                if truncated_candidate is not None:
                    candidate_date, candidate_items = truncated_candidate
                    tight_entry = next(
                        (
                            item
                            for item in run.date_crop_entries
                            if item.get("crop_key") == "tight"
                            and not item.get("audit_only")
                        ),
                        None,
                    )
                    try:
                        day_slot_views = (
                            _save_date_slot_views(tight_entry["line_raw"], run.temp_dir)
                            if tight_entry is not None
                            else {}
                        )
                    except Exception:
                        day_slot_views = {}
                    day_view = day_slot_views.get("day_digits", {})
                    day_variants = _recognize_day_slot_variants(day_view)
                    if _day_slot_confirms_value(day_variants, candidate_date.day):
                        truncated_mismatch_confirmation = (
                            candidate_date,
                            candidate_items,
                            day_view,
                            day_variants,
                        )
            if truncated_mismatch_confirmation is not None:
                (
                    mismatch_date,
                    mismatch_pair,
                    day_view,
                    day_variants,
                ) = truncated_mismatch_confirmation
                for item in mismatch_pair:
                    crop_entry = item["entry"]
                    item["variant"]["accepted_texts"] = item["raw_texts"]
                    item["variant"]["acceptance_note"] = (
                        "Mobile/Server 紧裁与宽裁四单元完整一致；"
                        "日数字窄槽双模型、双预处理确认两位日"
                    )
                    lx, ly, lw, lh = crop_entry["line_box"]
                    x, y, width, height = crop_entry["region_box"]
                    for row in item["rows"]:
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
                prefix = run.artifact_url_prefix.rstrip("/")
                for artifact in run.artifacts:
                    if artifact.get("variant") not in {"紧凑区域", "宽区域"}:
                        continue
                    artifact.update(
                        {
                            "date_day_slot_consensus_candidate": (
                                mismatch_date.isoformat()
                            ),
                            "date_day_slot_consensus_note": (
                                "Mobile/Server 在紧裁、宽裁四个最大通道单元"
                                "读到同一完整不匹配日期；若有其他完整候选，"
                                "只能是两位日漏掉一位，日数字窄槽双模型、"
                                "双预处理均保留两位数字"
                            ),
                        }
                    )
                tight_artifact = next(
                    (
                        artifact
                        for artifact in run.artifacts
                        if artifact.get("variant") == "紧凑区域"
                    ),
                    None,
                )
                if tight_artifact is not None and day_view:
                    tight_artifact.update(
                        {
                            "date_slot_day_digit_original_url": (
                                f"{prefix}/date/" f"{day_view['original'].name}"
                            ),
                            "date_slot_day_digit_processed_url": (
                                f"{prefix}/date/" f"{day_view['processed'].name}"
                            ),
                            "date_slot_day_digit_line_clean_url": (
                                f"{prefix}/date/" f"{day_view['line_clean'].name}"
                            ),
                            "date_slot_day_ocr_variants": day_variants,
                            "date_slot_day_candidate": str(mismatch_date.day),
                            "date_slot_day_reliable": True,
                            "date_slot_day_acceptance_note": (
                                "Mobile/Server 在最大通道去彩色及去横线"
                                "两种日数字窄槽上均只读到完整两位日；"
                                "候选不使用要求到货日期补值"
                            ),
                        }
                    )
            if mismatch_confirmation is not None:
                mismatch_date, mismatch_pair = mismatch_confirmation
                for item in mismatch_pair:
                    crop_entry = item["entry"]
                    item["variant"]["accepted_texts"] = item["raw_texts"]
                    item["variant"]["acceptance_note"] = (
                        "Mobile 与 Server 在紧/宽不同最大通道裁剪上"
                        "读到同一完整不匹配日期"
                    )
                    lx, ly, lw, lh = crop_entry["line_box"]
                    x, y, width, height = crop_entry["region_box"]
                    for row in item["rows"]:
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
                    artifact_label = "紧凑区域" if crop_entry["tight"] else "宽区域"
                    for artifact in run.artifacts:
                        if artifact.get("variant") == artifact_label:
                            artifact.update(
                                {
                                    "date_max_channel_mismatch_candidate": (
                                        mismatch_date.isoformat()
                                    ),
                                    "date_max_channel_mismatch_note": (
                                        "候选完全来自 Mobile/Server 最大通道"
                                        "日期行，不使用要求到货日期补值"
                                    ),
                                }
                            )
