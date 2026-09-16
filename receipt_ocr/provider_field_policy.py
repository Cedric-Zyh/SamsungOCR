"""Temporary acceptance of completed DanZhengTong field extraction."""

from copy import deepcopy


DZT_LAYOUT_REASONS = {
    "仅单证通识别，文档版式待复核",
    "仅执行远程印章识别，未验证文档类型",
}


def accept_real_dzt_fields(document_type, *, fields, trace):
    """Accept returned fields without inventing document classification evidence."""
    document = deepcopy(document_type or {})
    if (not isinstance(trace, dict) or trace.get("mode") != "real"
            or trace.get("simulated") is not False or trace.get("status") != "completed"
            or not isinstance(fields, dict)
            or not any(value is not None and str(value).strip() for value in fields.values())):
        return document
    document.update(provider_fields_accepted=True, source="单证通",
                    acceptance_policy="temporary_trust")
    if document.get("type") == "unclassified":
        document["reasons"] = [reason for reason in document.get("reasons", [])
                               if reason not in DZT_LAYOUT_REASONS]
    return document
