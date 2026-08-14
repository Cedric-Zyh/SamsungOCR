from receipt_ocr.document_types import classify_document
from receipt_ocr.ocr_types import TextObservation


def rows(*texts):
    return [TextObservation(text, .95, .05, .02 * i, .3, .01) for i, text in enumerate(texts)]


def test_standard_receipt_is_identified_from_title_and_business_fields():
    result = classify_document(rows("出 库 单", "承运商：顺丰", "运单号：W123", "客户订单号：727"))
    assert result["type"] == "receipt"
    assert result["reliable"] is True


def test_receipt_can_be_identified_when_title_ocr_is_missing():
    result = classify_document(rows("承运商", "运单号", "客户订单号", "客户名称", "要求到货"))
    assert result["type"] == "receipt"


def test_product_table_without_cover_fields_is_a_continuation_page():
    result = classify_document(rows("行号", "产品类别", "物料编号", "出库仓库", "数量", "EAN码", "40", "400"))
    assert result["type"] == "product_continuation"
    assert result["reliable"] is True


def test_headerless_multirow_product_page_is_a_continuation_page():
    observations = []
    for index, y in enumerate((.04, .06, .08), 1):
        observations.extend([
            TextObservation(str(index * 10), .99, .05, y, .03, .01),
            TextObservation(f"EF-X{index}00ABCGCN保护壳", .99, .23, y, .19, .01),
            TextObservation(f"88060957027{index:02d}", .99, .84, y, .11, .01),
        ])
    result = classify_document(observations)
    assert result["type"] == "product_continuation"
    assert result["evidence"]["continuation_rows"]["complete_rows"] == 3


def test_warehouse_authorization_title_has_priority_over_table_noise():
    result = classify_document(rows("仓库货物接收委托书", "本公司全权委托", "仓库联系人", "数量"))
    assert result["type"] == "warehouse_authorization"
    assert result["confidence"] == .99


def test_unknown_page_is_not_forced_into_receipt_template():
    result = classify_document(rows("扫描件", "备注"))
    assert result["type"] == "unknown"
    assert result["reliable"] is False
