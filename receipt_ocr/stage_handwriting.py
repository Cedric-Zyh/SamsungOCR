"""Optional receiving-entry recognition, separate from printed fields and date."""

from .requested_fields import handwritten_candidates


def execute(context, request):
    rows = context.page(request.route["page"])
    fields = {"仓库接收人": ""}
    if context.document_type["type"] in {"receipt", "product_continuation"}:
        fields.update(handwritten_candidates(rows))
    metadata = {name: {"value": value, "original": value, "confidence": .5,
                       "low_confidence": True, "source": "签收栏文字候选，手写内容待人工确认"}
                for name, value in fields.items() if value}
    return {"handwriting_fields": fields, "handwriting_metadata": metadata,
            "stage_review_reasons": ["签收填写内容需人工确认"] if metadata else []}
