"""Recognize product rows independently of other stages."""

from __future__ import annotations
from ..application.context import DocumentContext
from ..application.requests import StageRequest
from ..domain.parsing import parse_product_table
from ..domain.products.rules import _fuse_product_descriptions
from ..recognition.products.fallbacks import _recover_missing_product_grades


from ..domain.results import ProductStageResult


def recognize(context: DocumentContext, request: StageRequest) -> ProductStageResult:
    source, rows = context.source, context.page(request.route["page"])
    stage_backends = request.route
    detail_backend = stage_backends["date"]
    detail_page_rows = (
        context.cached_page(detail_backend)
        if detail_backend != stage_backends["page"]
        else []
    )
    product_table = parse_product_table(rows)
    if context.document_type["type"] == "receipt":
        product_table = _recover_missing_product_grades(
            source, rows, product_table, detail_backend, context.text_recognizer
        )
        if detail_page_rows and product_table.get("rows"):
            _fuse_product_descriptions(
                product_table, parse_product_table(detail_page_rows)
            )
    return ProductStageResult(table=product_table)
