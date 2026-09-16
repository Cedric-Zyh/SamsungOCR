"""Publish date-slot images, model readings and acceptance provenance."""

from __future__ import annotations
from .date_evidence import _required_month_slot_conflict_consensus_from_artifacts
from .date_crop_state import DateCropRun, DateSlotProbe


def _publish_slot_probe(run: DateCropRun, probe: DateSlotProbe) -> None:
    tight_artifact = next(
        (item for item in run.artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight_artifact is not None and probe.slot_views:
        prefix = run.artifact_url_prefix.rstrip("/")
        tight_artifact.update(
            {
                "date_slot_year_original_url": (
                    f"{prefix}/date/" f"{probe.year_view['original'].name}"
                ),
                "date_slot_year_processed_url": (
                    f"{prefix}/date/" f"{probe.year_view['processed'].name}"
                ),
                "date_slot_year_line_clean_url": (
                    f"{prefix}/date/" f"{probe.year_view['line_clean'].name}"
                ),
                "date_slot_month_context_original_url": (
                    f"{prefix}/date/" f"{probe.month_context_view['original'].name}"
                ),
                "date_slot_month_context_processed_url": (
                    f"{prefix}/date/" f"{probe.month_context_view['processed'].name}"
                ),
                "date_slot_day_context_original_url": (
                    f"{prefix}/date/" f"{probe.day_context_view['original'].name}"
                ),
                "date_slot_day_context_processed_url": (
                    f"{prefix}/date/" f"{probe.day_context_view['processed'].name}"
                ),
                "date_slot_adaptive_day_original_url": (
                    f"{prefix}/date/" f"{probe.adaptive_day_view['original'].name}"
                    if probe.adaptive_day_view
                    else ""
                ),
                "date_slot_adaptive_day_processed_url": (
                    f"{prefix}/date/" f"{probe.adaptive_day_view['processed'].name}"
                    if probe.adaptive_day_view
                    else ""
                ),
                "date_slot_day_context_white_url": (
                    f"{prefix}/date/"
                    f"{probe.day_context_view['white_processed'].name}"
                ),
                "date_slot_month_day_original_url": (
                    f"{prefix}/date/" f"{probe.month_day_view['original'].name}"
                ),
                "date_slot_month_day_processed_url": (
                    f"{prefix}/date/" f"{probe.month_day_view['processed'].name}"
                ),
                "date_slot_month_day_line_clean_url": (
                    f"{prefix}/date/" f"{probe.month_day_view['line_clean'].name}"
                ),
                "date_slot_year_white_url": (
                    f"{prefix}/date/" f"{probe.year_view['white_processed'].name}"
                ),
                "date_slot_month_day_vision_url": (
                    f"{prefix}/date/"
                    f"{probe.month_day_view['crop_first_white_processed'].name}"
                ),
                "date_slot_month_digit_original_url": (
                    f"{prefix}/date/" f"{probe.month_digit_view['original'].name}"
                    if probe.month_digit_view
                    else ""
                ),
                "date_slot_month_digit_processed_url": (
                    f"{prefix}/date/" f"{probe.month_digit_view['processed'].name}"
                    if probe.month_digit_view
                    else ""
                ),
                "date_slot_month_digit_line_clean_url": (
                    f"{prefix}/date/" f"{probe.month_digit_view['line_clean'].name}"
                    if probe.month_digit_view
                    else ""
                ),
                "date_slot_ocr_variants": probe.slot_variants,
                "date_slot_candidate": (
                    probe.slot_component_candidate.isoformat()
                    if probe.slot_component_candidate
                    else ""
                ),
                "date_adaptive_day_slot_candidate": (
                    probe.truncated_day_candidate.isoformat()
                    if (
                        probe.truncated_day_candidate is not None
                        and probe.reliable_slot_date == probe.truncated_day_candidate
                    )
                    else ""
                ),
                "date_slot_reliable": bool(probe.reliable_slot_date),
                "date_slot_acceptance_note": (
                    (
                        "Server 唯一完整日期获紧裁/宽裁漏一位日"
                        "证据支持；自适应日位原图、去章色图由"
                        " Mobile/Server 四单元一致确认；可自动核验"
                        if (
                            probe.truncated_day_candidate is not None
                            and probe.truncated_day_candidate
                            == probe.reliable_slot_date
                        )
                        else (
                            "Server 紧裁/宽裁完整日期一致，且"
                            "Mobile/Server 最大通道年份、月日"
                            "上下文槽位一致；可自动核验"
                            if probe.server_component_date is not None
                            else (
                                "Server 完整日期与同裁剪 Mobile 完整"
                                "年份、两位日一致；Mobile 只漏月份，"
                                "月份窄槽由双模型三个单元确认；可自动核验"
                                if probe.same_geometry_missing_month_date is not None
                                else (
                                    "Mobile/Server 在去彩色及去横线槽位"
                                    "独立组成要求日期，且无其他日期冲突；"
                                    "可自动核验"
                                    if probe.reliable_slot_date is not None
                                    else (
                                        "Mobile 在紧凑/宽区域重复同一四位"
                                        "年份日期，Server 重复要求日期；完整"
                                        "年份与白边日上下文经双模型确认 Mobile"
                                        "日期。整行模型仍冲突，低置信度标黄并"
                                        "进入人工复核"
                                        if probe.white_day_audit_date is not None
                                        else (
                                            "Paddle Mobile/Server 完整年份一致，"
                                            "Vision 单路径月日建议；低置信度标黄，"
                                            "必须人工复核"
                                            if "Vision 单路径"
                                            in probe.slot_candidate_source
                                            else "年份检测与月日整行识别均经 Mobile/Server 一致；"
                                            "同一物理日期行的槽位组合仅供人工复核"
                                        )
                                    )
                                )
                            )
                        )
                    )
                    if probe.slot_component_candidate
                    else "槽位组件未形成跨模型完整一致日期"
                ),
                "date_slot_candidate_source": (
                    probe.slot_candidate_source
                    if probe.slot_component_candidate
                    else ""
                ),
                "date_server_component_candidate": (
                    probe.server_component_date.isoformat()
                    if probe.server_component_date
                    else ""
                ),
                "date_server_component_note": (
                    "Server 在紧裁/宽裁最大通道图重复同一"
                    "完整日期；Mobile/Server 固定年份与月日"
                    "上下文槽位逐组件一致，其他残缺候选仅"
                    "改变月份"
                    if probe.server_component_date
                    else ""
                ),
                "date_missing_month_consensus_candidate": (
                    probe.same_geometry_missing_month_date.isoformat()
                    if probe.same_geometry_missing_month_date
                    else ""
                ),
                "date_missing_month_consensus_note": (
                    "Server 完整日期与同裁剪 Mobile 漏月份"
                    "日期一致；月份数字窄槽由 Mobile/Server"
                    " 至少三个单元确认，唯一干扰为两位日"
                    "的单字截断"
                    if probe.same_geometry_missing_month_date
                    else ""
                ),
                "date_white_day_audit_candidate": (
                    probe.white_day_audit_date.isoformat()
                    if probe.white_day_audit_date
                    else ""
                ),
                "date_white_day_audit_conflict": (
                    probe.white_day_conflict_prefilter["conflict"].isoformat()
                    if probe.white_day_audit_date is not None
                    and probe.white_day_conflict_prefilter
                    else ""
                ),
                "date_white_day_audit_note": (
                    "Mobile 双几何整行与双模型完整年份、"
                    "两种白边日上下文均为同一候选；Server"
                    "双几何整行仍为要求日期，因此只作人工"
                    "复核建议，不自动核验"
                    if probe.white_day_audit_date
                    else ""
                ),
            }
        )
        month_conflict_consensus = (
            _required_month_slot_conflict_consensus_from_artifacts(
                run.artifacts, run.required_text
            )
        )
        if month_conflict_consensus is not None:
            for artifact in run.artifacts:
                if artifact.get("variant") in {
                    "紧凑区域",
                    "宽区域",
                }:
                    artifact.update(
                        {
                            "date_month_slot_conflict_candidate": (
                                month_conflict_consensus["date"].isoformat()
                            ),
                            "date_month_slot_conflict_note": (
                                "Mobile/Server 在相反紧宽裁剪"
                                "读到同一完整要求日期；唯一"
                                "完整冲突来自 Server 去线图且"
                                "只改变月份，月份数字窄槽经"
                                "双模型、双预处理逐格确认"
                            ),
                            "date_month_slot_discarded_conflict": (
                                month_conflict_consensus["conflict"].isoformat()
                            ),
                        }
                    )
