"""The nine requested output fields, grouped by recognition responsibility."""

import re

OUTPUT_FIELDS = (
    "仓库联系人", "拒收数量", "实收数量", "仓库接收人", "签收日期",
    "签章要求", "合计数量", "客户名称", "要求到货",
)
PRINTED_FIELDS = ("仓库联系人", "拒收数量", "实收数量", "合计数量", "客户名称", "要求到货", "签章要求")
HANDWRITTEN_FIELDS = ("仓库接收人",)
PENDING_PROVIDER_FIELDS = ("拒收数量", "实收数量")


def _contact_names(value):
    """Return the separately recognized contact names in a field value."""
    text = str(value or "").strip()
    if not text:
        return []
    # The extractor uses 、; accept common separators too for old records or
    # manually edited values.
    parts = re.split(r"[、,，/／;；|]+", text)
    return [part.strip() for part in parts if part.strip()]


def derive_signature_check(fields, previous=None):
    """Compare the printed warehouse contact with the handwritten receiver.

    The final verdict policy decides whether and how to aggregate signature
    evidence; this helper only compares one pair of recognized fields.
    Preserve an explicit human choice or saved policy aggregation while the
    two source fields are unchanged. Reading a result must not replace a
    skipped or multi-provider check with this single pair's comparison.
    """
    fields = fields or {}
    contact = str(fields.get("仓库联系人") or "").strip()
    receiver = str(fields.get("仓库接收人") or "").strip()
    prior = previous if isinstance(previous, dict) else {}
    if (prior.get("source") == "人工复核"
            and prior.get("contact") == contact
            and prior.get("receiver") == receiver
            and isinstance(prior.get("human_confirmed_match"), bool)):
        return {**prior, "contact": contact, "receiver": receiver}
    mode, status = prior.get("match_mode"), prior.get("status")
    if (prior.get("contact") == contact
            and prior.get("receiver") == receiver
            and isinstance(prior.get("reliable"), bool)
            and ((mode == "none" and status == "未核对")
                 or (mode in ("any", "all") and status in ("匹配", "不匹配", "需人工复核")))):
        return {**prior, "contact": contact, "receiver": receiver}
    normalized = lambda value: "".join(str(value).split())
    if contact and receiver:
        receiver_name = normalized(receiver)
        contact_names = [normalized(name) for name in _contact_names(contact)]
        # Multiple contacts are common on a dispatch line.  One exact name
        # match is sufficient; do not require every contact to match the
        # handwritten receiver.
        status = "匹配" if receiver_name in contact_names else "不匹配"
        reliable = True
    else:
        status = "未识别"
        reliable = False
    return {
        "contact": contact,
        "receiver": receiver,
        "status": status,
        "reliable": reliable,
        "source": "字段比对",
    }


def project_fields(result):
    """Preserve internal evidence for date checks/retries; expose only the contract."""
    fields = result.get("fields") or {}
    internal = {**result.get("internal_fields", {}), **fields}
    result["internal_fields"] = {k: v for k, v in internal.items() if k not in OUTPUT_FIELDS}
    result["fields"] = {name: str(fields.get(name) or "") for name in OUTPUT_FIELDS}
    result["signature_check"] = derive_signature_check(
        result["fields"], result.get("signature_check")
    )
    # One source of truth for date recognition and human correction.
    date_check = result.get("date_check") or {}
    # Keep a complete ISO date in ``date_check.actual`` for business logic,
    # while exposing OCR-owned partial components (for example
    # ``2026-02-__``) instead of collapsing them to a blank field.
    result["fields"]["签收日期"] = str(
        date_check.get("actual_display") or date_check.get("actual") or ""
    )
    for key in ("field_metadata", "field_fallbacks"):
        result[key] = {k: v for k, v in (result.get(key) or {}).items() if k in OUTPUT_FIELDS}
    for variant in result.get("recognition_variants", {}).get("fields", []):
        details = variant.get("details") or {}
        for key in ("fields", "field_metadata"):
            if isinstance(details.get(key), dict):
                details[key] = {k: v for k, v in details[key].items() if k in PRINTED_FIELDS}
    result["field_schema_version"] = 1
    return result


def recognition_fields(result):
    return {**result.get("internal_fields", {}), **result.get("fields", {})}
