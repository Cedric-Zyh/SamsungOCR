"""Three-way seal decisions for samples, independent of review workflow."""
from .parsing.parsing_seals import compare_seal_text_strict

SAMPLE_SEAL_DECISIONS = {"匹配", "部分匹配", "不匹配"}


def sample_seal_decision(check, *, requirement="", value=None):
    status = check.get("status")
    if status in SAMPLE_SEAL_DECISIONS:
        return status
    if status == "匹配待确认":
        return "匹配"
    value = str(value if value is not None else
                check.get("recognized") or check.get("display_text") or "").strip()
    if not value:
        return "不匹配"
    requirement = check.get("requirement") or requirement
    if requirement:
        decision = compare_seal_text_strict(requirement, [value])["status"]
        if decision in SAMPLE_SEAL_DECISIONS:
            return decision
    return "部分匹配"
