"""Machine-readable review reasons; display text never selects a policy."""
from typing import Literal, TypedDict


class ReviewIssue(TypedDict):
    code: str
    scope: str
    message: str
    blocking: bool
    provider: str


class CheckResult(TypedDict, total=False):
    status: Literal["未执行", "未识别", "匹配", "不匹配", "部分匹配", "需人工复核", "缺少比对依据", "识别失败", "未核对"]
    reliable: bool
    confidence: float
    actual: str
    recognized: str
    message: str


def issue(code: str, scope: str, message: str, *, blocking=True, provider="") -> ReviewIssue:
    return dict(code=code, scope=scope, message=message, blocking=blocking, provider=provider)


def active_issues(issues: list[ReviewIssue], acceptance: dict) -> list[ReviewIssue]:
    result = []
    for item in issues:
        if not item["blocking"]:
            continue
        scope = item["scope"]
        if scope in {"date", "seal", "signature"} and acceptance.get(f"{scope}_match_mode") == "none":
            continue
        if item["code"] == "low_confidence" and acceptance.get("low_confidence_mode") == "ignore":
            continue
        if item not in result:
            result.append(item)
    return result
