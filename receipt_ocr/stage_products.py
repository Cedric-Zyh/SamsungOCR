"""Recognize product rows independently of other stages."""

from __future__ import annotations
from .document_context import DocumentContext, StageRequest
from .parser import parse_product_table
from .product_rules import _fuse_product_descriptions, _recover_missing_product_grades


def execute(context: DocumentContext, request: StageRequest):
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
            source, rows, product_table, detail_backend
        )
        if detail_page_rows and product_table.get("rows"):
            _fuse_product_descriptions(
                product_table, parse_product_table(detail_page_rows)
            )
    return {"product_table": product_table, "stage_review_reasons": []}
