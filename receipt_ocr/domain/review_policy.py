"""Protect saved human decisions when refreshing machine evidence."""

def has_human_edits(result):
    """Skip the whole record when any saved human decision could be affected."""
    if (result.get("review_status") in {"确认通过", "确认不通过"}
            or result.get("human_note") or result.get("page_review_override")):
        return True
    checks = [result.get(key) or {} for key in ("date_check", "seal_check", "signature_check")]
    metadata = list((result.get("field_metadata") or {}).values())
    for evidence in [*checks, *metadata]:
        if ("人工" in str(evidence.get("source", ""))
                or "人工" in str(evidence.get("backend", ""))
                or evidence.get("human_confirmed")
                or "human_confirmed_match" in evidence):
            return True
    rows = (result.get("product_table") or {}).get("rows") or []
    return any("人工" in str(value) for row in rows for value in (row.get("sources") or {}).values())
