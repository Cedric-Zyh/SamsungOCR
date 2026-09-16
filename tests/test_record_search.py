"""Global search uses complete logical receipts and composes with other filters."""
import pytest

from receipt_ocr.database import _matches_filters


@pytest.mark.parametrize("query", ["发货", "Invoice-73", "客户甲", "ORD-19"])
def test_search_finds_filename_linked_page_customer_and_internal_order(query):
    item = {
        "filename": "发货回单.jpg",
        "source_filenames": ["folder/Invoice-7302-page-2.jpg"],
        "fields": {"客户名称": "华东客户甲"},
        "internal_fields": {"客户订单号": "ORD-1902"},
    }
    assert _matches_filters(item, {"search": query})
    assert not _matches_filters(item, {"search": query, "customer": "客户乙"})


def test_search_is_literal_case_insensitive_and_does_not_match_unrelated_fields():
    item = {"filename": "A_50%.jpg", "fields": {"客户名称": "甲", "签章要求": "秘密标记"}}
    assert _matches_filters(item, {"search": " a_50% "})
    assert not _matches_filters(item, {"search": "秘密标记"})
    assert not _matches_filters(item, {"search": "a.*"})
