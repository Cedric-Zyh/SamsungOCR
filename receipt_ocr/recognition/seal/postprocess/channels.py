"""Keep local OCR independent of the provider that located the stamp."""
from __future__ import annotations

from copy import deepcopy

LOCAL_REGION_SOURCE = "清瞳印章区域"


def local_seal_full_match(check) -> bool:
    """Compatibility helper: an actual strict local match, from any region."""
    return bool(
        isinstance(check, dict)
        and check.get("comparison_policy") in {"strict_text", "strict_information_fields"}
        and check.get("status") == "匹配"
        and check.get("reliable") is True
        and not check.get("simulated")
    )


def record_local_channel(verdict: dict, local_check, match_mode: str = "any") -> dict:
    """Rank independent readings uniformly, retaining both original checks."""
    if not isinstance(local_check, dict) or not local_check:
        return verdict
    from .providers import combine_seal_provider_checks

    result = combine_seal_provider_checks([verdict, local_check], match_mode=match_mode)
    if result is not None:
        return result
    return {**verdict, "local_channel": deepcopy(local_check)}
