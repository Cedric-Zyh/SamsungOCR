"""Dispatch local recognition stages and return stage-owned domain results."""

from dataclasses import replace

from ..domain.results import StageResult
from ..application.requests import StageRequest
from ..application.context import DocumentContext
from ..stages import fields, products, handwriting, date, seal
from .qingtong_dispatch import QingTongDispatch


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
        if request.seal_mode == "qingtong" and request.seal_evidence is None:
            # The legacy combined entry point routes its page before upload.
            # Pass those same rows onward instead of recognizing/reusing twice.
            request = replace(request, seal_page_rows=tuple(context.page(request.route["page"])))
        request = QingTongDispatch(analyzer.seal_api, context.source).bind(context, request)
        return seal.recognize(context, request, analyzer._recognize_local_seals)
    raise ValueError(f"未知识别阶段：{stage}")
