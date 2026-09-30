"""Validation and serialization of durable web recognition settings."""

from __future__ import annotations


CURRENT_PROVIDER_IDS = frozenset({
    "paddle_v6", "paddle_seal", "qingtong", "danzhengtong",
})
RECOGNITION_STAGES = ("fields", "products", "handwriting", "date", "seal")
ORIENTATION_MODES = frozenset({"none", "polygon", "doc_ori", "combined"})
MATCH_MODES = frozenset({"any", "all", "none"})
REJECT_MODES = frozenset({"any_mismatch", "all_mismatch", "none"})


def normalize_recognition_settings(payload: object) -> dict:
    """Keep durable settings limited to values emitted by the settings UI."""
    if not isinstance(payload, dict):
        raise ValueError("识别配置必须是对象")
    plan = payload.get("recognition_config")
    policy = payload.get("acceptance_policy")
    if not isinstance(plan, dict) or not isinstance(policy, dict):
        raise ValueError("请同时提供识别方案和核验规则")
    normalized_plan = {}
    for stage in RECOGNITION_STAGES:
        methods = plan.get(stage, [])
        if (not isinstance(methods, list)
                or any(not isinstance(method, str) or method not in CURRENT_PROVIDER_IDS
                       for method in methods)):
            raise ValueError("识别方式配置无效")
        normalized_plan[stage] = list(dict.fromkeys(methods))
    orientation = plan.get("seal_orientation", "polygon")
    if orientation not in ORIENTATION_MODES:
        raise ValueError("印章方向配置无效")
    normalized_plan["seal_orientation"] = orientation
    normalized_policy = {
        "seal_match_mode": policy.get("seal_match_mode", "any"),
        "date_match_mode": policy.get("date_match_mode", "any"),
        "signature_match_mode": policy.get("signature_match_mode", "none"),
        "reject_mode": policy.get("reject_mode", "any_mismatch"),
        "low_confidence_mode": policy.get("low_confidence_mode", "ignore"),
        # These keys remain part of the stored contract for older task rows.
        "seal_pass_standard": "any_exact",
        "date_source": "danzhengtong",
    }
    if any(normalized_policy[key] not in MATCH_MODES
           for key in ("seal_match_mode", "date_match_mode", "signature_match_mode")):
        raise ValueError("匹配规则配置无效")
    if (normalized_policy["reject_mode"] not in REJECT_MODES
            or normalized_policy["low_confidence_mode"] not in {"check", "ignore"}):
        raise ValueError("复核规则配置无效")
    return {
        "recognition_config": normalized_plan,
        "acceptance_policy": normalized_policy,
    }


__all__ = ["normalize_recognition_settings"]
