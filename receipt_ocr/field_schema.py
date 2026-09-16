"""The nine requested output fields, grouped by recognition responsibility."""

OUTPUT_FIELDS = (
    "仓库联系人", "拒收数量", "实收数量", "仓库接收人", "签收日期",
    "签章要求", "合计数量", "客户名称", "要求到货",
)
PRINTED_FIELDS = ("仓库联系人", "拒收数量", "实收数量", "合计数量", "客户名称", "要求到货", "签章要求")
HANDWRITTEN_FIELDS = ("仓库接收人",)
PENDING_PROVIDER_FIELDS = ("拒收数量", "实收数量")


def derive_signature_check(fields, previous=None):
    """Compare the printed warehouse contact with the handwritten receiver.

    Signature evidence is deliberately separate from the date/seal verdict:
    it is useful for review and audit, but it is not an acceptance gate.
    Preserve an explicit human choice while the two source fields are
    unchanged so reopening a review does not silently discard it.
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
    normalized = lambda value: "".join(str(value).split())
    if contact and receiver:
        status = "匹配" if normalized(contact) == normalized(receiver) else "不匹配"
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
    result["fields"]["签收日期"] = str((result.get("date_check") or {}).get("actual") or "")
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
