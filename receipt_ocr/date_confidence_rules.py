"""Ordered date confidence rules and final review-only caps."""

from __future__ import annotations
from .parser import estimate_date_confidence, parse_date
from .date_evidence import _server_mobile_dominant_date_from_artifacts
from .date_decision import DateStageEvidence, DateDecision, DateConsensus


def _score_primary_evidence(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    # Confidence must come from the dedicated, user-visible date crops.
    # The supplementary full-page Vision pass observes the same pixels and
    # is useful for finding a candidate, but counting it as independent
    # corroboration created a false 21→25 automatic match.
    decision.date_confidence = estimate_date_confidence(
        evidence.date_rows, evidence.fields.get("要求到货", ""), decision.actual_date
    )
    white_day_audit_candidates = {
        parsed
        for artifact in evidence.date_artifacts
        if (
            parsed := parse_date(
                str(artifact.get("date_white_day_audit_candidate", ""))
            )
        )
        is not None
    }
    if (
        decision.actual_date is not None
        and white_day_audit_candidates == {decision.actual_date}
        and decision.rejected_date is None
    ):
        # This route deliberately improves the review suggestion without
        # claiming an automatic decision: whole-line Mobile and Server
        # still disagree even though the isolated day context favors the
        # Mobile value.  Keep the confidence visibly below the 72% gate.
        decision.date_confidence = 0.45
        decision.date_check["white_day_conflict_audit"] = {
            "candidate": decision.actual_date.isoformat(),
            "models": ["mobile", "server"],
            "geometries": ["紧凑区域", "宽区域"],
            "policy": "整行冲突，仅作白边日上下文人工建议",
        }
        decision.date_check["message"] += (
            "（Mobile 双几何整行与白边日上下文支持该日期，"
            "但 Server 整行仍为另一日期；仅供人工复核）"
        )
    dominant_artifact_date = _server_mobile_dominant_date_from_artifacts(
        evidence.date_artifacts, evidence.fields.get("要求到货", "")
    )
    if (
        decision.actual_date is not None
        and dominant_artifact_date == decision.actual_date
    ):
        decision.date_confidence = max(0.78, decision.date_confidence)
        decision.date_check["message"] += (
            "（Server 完整日期获 Mobile 紧凑/宽区域重复支持；" "孤立干扰已保留供复核）"
        )
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_server_mobile_dominance_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_server_mobile_dominance_note": (
                            "唯一 Server 完整日期在要求到货日前后 3 天内，"
                            "Mobile 在紧凑/宽区域至少四路重复支持；"
                            "其余最多一个非完整 Mobile 孤立读数"
                        ),
                    }
                )
    if (
        decision.actual_date is not None
        and consensus.repeated_server_date == decision.actual_date
    ):
        decision.date_confidence = max(0.74, decision.date_confidence)
        decision.date_check[
            "message"
        ] += "（Server 在同一日期区域三种预处理均读到完整要求日期）"
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_repeated_server_candidate": decision.actual_date.isoformat(),
                        "date_repeated_server_note": (
                            "唯一可解析日期等于要求到货日期；Server 在同一"
                            "几何区域的原始、最大通道或自动对比等至少三种"
                            "预处理上严格重复"
                        ),
                    }
                )
    if (
        decision.actual_date is not None
        and consensus.server_mobile_component_date == decision.actual_date
    ):
        decision.date_confidence = max(0.82, decision.date_confidence)
        decision.date_check["message"] += (
            "（Server 紧裁/宽裁重复完整日期；Mobile 两个区域均保留"
            "同年月日组件，Mobile 最大通道冲突自身不一致，已保留供复核）"
        )
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_server_mobile_component_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_server_mobile_component_note": (
                            "Server 最大通道在紧裁、宽裁只重复同一完整日期；"
                            "Mobile 两个区域的非完整原文都独立保留同年月日；"
                            "Mobile 最大通道的两个完整冲突值月日相同但年份"
                            "互不一致，不能否决跨模型组件共识"
                        ),
                    }
                )


def _score_confirmed_audit_markers(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    day_slot_candidates = {
        parsed
        for artifact in evidence.date_artifacts
        if (
            parsed := parse_date(
                str(artifact.get("date_day_slot_consensus_candidate", ""))
            )
        )
        is not None
    }
    if (
        decision.actual_date is not None
        and day_slot_candidates == {decision.actual_date}
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.86, decision.date_confidence)
        decision.date_check["day_slot_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "day": decision.actual_date.day,
            "models": ["mobile", "server"],
            "preprocessings": ["最大通道去彩色", "最大通道去彩色并去横线"],
        }
        decision.date_check["message"] += (
            "（Mobile/Server 紧裁与宽裁四单元完整一致；"
            "日数字窄槽双模型、双预处理确认两位日）"
        )
    adaptive_day_candidates = {
        parsed
        for artifact in evidence.date_artifacts
        if (
            parsed := parse_date(
                str(artifact.get("date_adaptive_day_slot_candidate", ""))
            )
        )
        is not None
    }
    if (
        decision.actual_date is not None
        and adaptive_day_candidates == {decision.actual_date}
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.84, decision.date_confidence)
        decision.date_check["adaptive_day_slot_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "day": decision.actual_date.day,
            "models": ["mobile", "server"],
            "preprocessings": ["原始裁剪", "最大通道去彩色"],
        }
        decision.date_check["message"] += (
            "（Server 唯一完整日期获紧/宽漏一位日证据支持；"
            "自适应日位裁剪经 Mobile/Server 双图一致确认）"
        )
    separator_candidates = {
        parsed
        for artifact in evidence.date_artifacts
        if (
            parsed := parse_date(
                str(artifact.get("date_missing_year_separator_candidate", ""))
            )
        )
        is not None
    }
    if (
        decision.actual_date is not None
        and separator_candidates == {decision.actual_date}
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.84, decision.date_confidence)
        decision.date_check["missing_year_separator_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "day": decision.actual_date.day,
            "models": ["mobile", "server"],
            "preprocessings": ["去单位最大通道", "去单位最大通道并去横线"],
        }
        decision.date_check["message"] += (
            "（Mobile 自包含日期仅漏印刷“年”，Server 完整日期一致；"
            "去单位日数字窄槽双模型、双预处理确认两位日）"
        )
    nondestructive_cross_year_candidates = {
        parsed
        for artifact in evidence.date_artifacts
        if (
            parsed := parse_date(
                str(artifact.get("date_cross_year_nondestructive_candidate", ""))
            )
        )
        is not None
    }
    if (
        decision.actual_date is not None
        and nondestructive_cross_year_candidates == {decision.actual_date}
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.88, decision.date_confidence)
        decision.date_check["cross_year_nondestructive_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "models": ["mobile", "server"],
            "geometries": ["紧凑区域", "宽区域"],
            "policy": "Mobile 非破坏性八单元 + Server 宽裁独立单元",
        }
        decision.date_check["message"] += (
            "（Mobile 紧裁/宽裁非破坏性原图与去章色图均为同一"
            "完整跨年日期；Server 宽裁独立确认）"
        )


def _score_complete_consensus(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    if (
        decision.actual_date is not None
        and consensus.cross_year_consensus is not None
        and consensus.cross_year_consensus["date"] == decision.actual_date
    ):
        decision.date_confidence = max(0.86, decision.date_confidence)
        decision.date_check["message"] += (
            "（Paddle Mobile、Paddle Server 在紧裁和宽裁均读到同一"
            "完整跨年日期；缺年份读数未用于覆盖字面年份）"
        )
        decision.date_check["cross_year_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "support": consensus.cross_year_consensus["support"],
            "strict_observation_count": consensus.cross_year_consensus[
                "strict_observation_count"
            ],
        }
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_cross_year_consensus_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_cross_year_consensus_note": (
                            "唯一完整四位年份日期获 Mobile/Server 紧裁与宽裁"
                            "四个必要单元共同支持；其他残缺读数月日一致"
                        ),
                    }
                )
    if (
        decision.actual_date is not None
        and consensus.component_consensus is not None
        and consensus.component_consensus["date"] == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.84, decision.date_confidence)
        decision.date_check["component_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "support": consensus.component_consensus["support"],
        }
        decision.date_check["message"] += (
            "（Paddle Mobile/Server 对完整年份、月份数字窄槽和显式日"
            "分别达成一致；无其他完整日期冲突）"
        )
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") == "紧凑区域":
                artifact.update(
                    {
                        "date_component_consensus_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_component_consensus_note": (
                            "完整年份、年/月之间的月份数字窄槽、显式日均由"
                            "Mobile/Server 独立一致识别；候选不使用要求日期补值"
                        ),
                    }
                )
    if (
        decision.actual_date is not None
        and consensus.server_component_consensus is not None
        and consensus.server_component_consensus["date"] == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.86, decision.date_confidence)
        decision.date_check["server_component_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "support": consensus.server_component_consensus["support"],
        }
        decision.date_check["message"] += (
            "（Server 紧裁/宽裁最大通道图重复同一完整日期；"
            "Mobile/Server 固定年份与月日上下文槽位逐组件一致）"
        )
    if (
        decision.actual_date is not None
        and consensus.month_slot_conflict_consensus is not None
        and consensus.month_slot_conflict_consensus["date"] == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.86, decision.date_confidence)
        decision.date_check["month_slot_conflict_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "discarded_conflict": (
                consensus.month_slot_conflict_consensus["conflict"].isoformat()
            ),
            "month": decision.actual_date.month,
            "models": ["mobile", "server"],
            "preprocessings": ["最大通道去彩色", "最大通道去彩色并去横线"],
        }
        decision.date_check["message"] += (
            "（Mobile/Server 跨几何完整要求日期一致；唯一去线冲突"
            "仅改变月份，月份数字窄槽经双模型、双预处理确认）"
        )
    if (
        decision.actual_date is not None
        and consensus.missing_month_consensus is not None
        and consensus.missing_month_consensus["date"] == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.84, decision.date_confidence)
        decision.date_check["missing_month_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "geometry": consensus.missing_month_consensus["geometry"],
            "supporting_month_cells": consensus.missing_month_consensus[
                "supporting_month_cells"
            ],
            "discarded_conflicts": [
                value.isoformat()
                for value in consensus.missing_month_consensus["conflicts"]
            ],
        }
        decision.date_check["message"] += (
            "（Server 读取完整日期；同裁剪 Mobile 保留完整年份与"
            "两位日但漏月份，月份窄槽由双模型三单元确认；唯一"
            "干扰是两位日的单字截断）"
        )


def _score_business_consensus(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    if (
        decision.actual_date is not None
        and consensus.partial_year_required_consensus is not None
        and consensus.partial_year_required_consensus["date"] == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.78, decision.date_confidence)
        decision.date_check["partial_year_required_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "geometry": "宽区域",
            "models": ["mobile", "server"],
            "year_sources": ["制单日期", "运单号", "要求到货"],
            "support": consensus.partial_year_required_consensus["support"],
        }
        decision.date_check["message"] += (
            "（Mobile 去表格线与 Server 多种增强均读到相同月日及"
            "三位年份；缺失的年份末位由制单日期、运单号和要求到货"
            "三项独立业务日期一致确认）"
        )
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") == "宽区域":
                artifact.update(
                    {
                        "date_partial_year_required_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_partial_year_required_note": (
                            "Mobile 宽裁去表格线与 Server 宽裁至少两种增强"
                            "读到相同 20x 年/月/日；制单日期、运单号和要求"
                            "到货年份一致，仅补年份末位，无其他合法日期冲突"
                        ),
                    }
                )
    if (
        decision.actual_date is not None
        and consensus.unanimous_month_day_business_year_consensus is not None
        and consensus.unanimous_month_day_business_year_consensus["date"]
        == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.82, decision.date_confidence)
        discarded = consensus.unanimous_month_day_business_year_consensus[
            "discarded_strict"
        ]
        decision.date_check["unanimous_month_day_business_year_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "discarded_strict": discarded.isoformat(),
            "models": ["mobile", "server"],
            "geometries": ["紧凑区域", "宽区域"],
            "year_sources": ["制单日期", "运单号", "要求到货"],
            "support": consensus.unanimous_month_day_business_year_consensus["support"],
        }
        decision.date_check["message"] += (
            "（Mobile/Server 与紧裁/宽裁的显式月日均为同一值；"
            "唯一完整年份候选早于制单日期一年以上，制单日期、"
            "运单号和要求到货三项独立业务日期一致确认年份）"
        )
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_unanimous_month_day_business_year_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_unanimous_month_day_business_year_discarded": (
                            discarded.isoformat()
                        ),
                        "date_unanimous_month_day_business_year_note": (
                            "跨模型、跨几何显式月日无冲突；唯一完整日期的"
                            "年份违反业务时间，三项独立业务日期仅修正年份"
                        ),
                    }
                )
    if (
        decision.actual_date is not None
        and consensus.partial_year_missing_month_business_consensus is not None
        and consensus.partial_year_missing_month_business_consensus["date"]
        == decision.actual_date
        and decision.rejected_date is None
    ):
        decision.date_confidence = max(0.84, decision.date_confidence)
        decision.date_check["partial_year_missing_month_business_consensus"] = {
            "candidate": decision.actual_date.isoformat(),
            "models": ["mobile", "server"],
            "geometries": ["紧凑区域", "宽区域"],
            "year_sources": ["制单日期", "运单号", "要求到货"],
            "day_support": (
                consensus.partial_year_missing_month_business_consensus["day_support"]
            ),
            "month_support": (
                consensus.partial_year_missing_month_business_consensus["month_support"]
            ),
        }
        decision.date_check["message"] += (
            "（Mobile/Server 跨紧宽裁日期行一致保留日数字；"
            "Mobile 月份上下文与 Server 月份窄槽各两种预处理一致；"
            "残缺年份由制单日期、运单号和要求到货三项确认）"
        )
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_partial_year_missing_month_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_partial_year_missing_month_note": (
                            "跨模型、跨几何日数字一致；Mobile 宽月份上下文"
                            "与 Server 窄月份槽双预处理一致；三业务日期仅补年份"
                        ),
                    }
                )


def _apply_review_confidence_caps(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    otsu_manual_candidate = bool(
        decision.actual_date
        and any(
            "Otsu 三倍放大 Mobile/Server 人工候选"
            in str(variant.get("preprocessing", ""))
            and any(
                parse_date(text) == decision.actual_date
                for text in variant.get("accepted_texts", [])
            )
            for artifact in evidence.date_artifacts
            for variant in artifact.get("date_line_ocr_variants", [])
        )
    )
    if otsu_manual_candidate and consensus.cross_year_consensus is None:
        decision.date_confidence = min(0.45, decision.date_confidence)
        decision.date_check["message"] += "（Otsu 跨模型同日候选，待人工确认）"
    if decision.audit_date_candidate:
        decision.date_confidence = min(0.35, decision.date_confidence)
        decision.date_check["message"] += f"（{decision.audit_date_note}）"
    if decision.partial_year_day_before_selected:
        # This route intentionally reconstructs only the missing year
        # suffix from the printed document context. It must remain below
        # the automatic-decision threshold even if generic scoring or a
        # future auxiliary rule sees the same month/day.
        decision.date_confidence = 0.46
    decision.date_check["confidence"] = decision.date_confidence
    decision.date_check["reliable"] = bool(
        decision.actual_date and decision.date_confidence >= 0.72
    )
