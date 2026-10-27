"""Three-way seal decisions for samples, independent of review workflow."""
from .parsing.parsing_seals import compare_seal_text_strict
from .warehouse_code_policy import warehouse_code_adjustment

SAMPLE_SEAL_DECISIONS = {"匹配", "部分匹配", "不匹配"}


def _json_boolean(value):
    """Both JSON booleans and SQLite's compact 0/1 projection are explicit."""
    return bool(value) if isinstance(value, (bool, int)) and value in (0, 1) else None


def _blocked_receiving_candidate(check):
    """A displayed candidate is not an adopted physical-stamp identity."""
    return (_json_boolean(check.get('reliable')) is False
            and _json_boolean((check.get('receiving_review') or {}).get('required')) is True)


def _blocked_standard_decision(check):
    """A text-only policy cannot supply an unproven or mismatching shape."""
    return (check.get('comparison_policy') == 'manual_standard_text_and_shape'
            and check.get('status') != '匹配')


def sample_warehouse_code_adjustment(check, *, requirement="", value=None):
    """Apply only the requested code policy, preserving explicit human/shape decisions."""
    if (check.get('status') not in SAMPLE_SEAL_DECISIONS | {'待确认', '匹配待确认', '需人工复核'}
            or check.get('human_confirmed_match') is not None
            or check.get('comparison_policy') == 'manual_standard_text_and_shape'
            or check.get('simulated') or _blocked_receiving_candidate(check)):
        return None
    value = value if value is not None else check.get('recognized') or check.get('display_text') or ''
    return warehouse_code_adjustment(requirement or check.get('requirement') or '', value)


def sample_seal_decision(check, *, requirement="", value=None):
    status = check.get("status")
    human = _json_boolean(check.get('human_confirmed_match'))
    if human is not None:
        return "匹配" if human else "不匹配"
    blocked = _blocked_receiving_candidate(check) or _blocked_standard_decision(check)
    if sample_warehouse_code_adjustment(check, requirement=requirement, value=value):
        return "匹配"
    if status in SAMPLE_SEAL_DECISIONS:
        return "部分匹配" if blocked and status == "匹配" else status
    if status == "匹配待确认":
        return "部分匹配" if blocked else "匹配"
    value = str(value if value is not None else
                check.get("recognized") or check.get("display_text") or "").strip()
    if not value:
        return "不匹配"
    requirement = check.get("requirement") or requirement
    if requirement:
        decision = compare_seal_text_strict(requirement, [value])["status"]
        if decision in SAMPLE_SEAL_DECISIONS:
            # apply_receiving_review may put an exact-looking candidate into
            # recognized solely for inspection. Only the receiving resolver
            # can prove that its other reliable observations also agree.
            return "部分匹配" if blocked and decision == "匹配" else decision
    return "部分匹配"
