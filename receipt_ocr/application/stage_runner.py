"""Dispatch local recognition stages and return stage-owned domain results."""

from ..domain.results import StageResult
from ..domain.requests import StageRequest
from ..application.context import DocumentContext
from ..stages import fields, products, handwriting, date, seal


def recognize_stage(analyzer, context: DocumentContext, stage: str,
                    request: StageRequest) -> StageResult:
    if stage == "fields":
        return fields.recognize(context, request)
    if stage == "products":
        return products.recognize(context, request)
    if stage == "handwriting":
        return handwriting.recognize(context, request)
    if stage == "date":
        return date.recognize(context, request, analyzer._recognize_receipt_date)
    if stage == "seal":
        return seal.recognize(context, request, analyzer._recognize_local_seals, analyzer.seal_api)
    raise ValueError(f"未知识别阶段：{stage}")
