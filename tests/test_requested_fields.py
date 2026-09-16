from types import SimpleNamespace

from receipt_ocr.field_schema import OUTPUT_FIELDS, project_fields, recognition_fields
from receipt_ocr.requested_fields import printed_extras, handwritten_candidates
from receipt_ocr.ocr_types import TextObservation as Row
from receipt_ocr import stage_handwriting


def row(text, x=.1, y=.5, width=.15):
    return Row(text, .99, x, y, width, .02)


def test_only_nine_outputs_with_date_from_its_own_stage():
    result = {"fields": {"客户名称": "客户", "签收日期": "错误旧值", "运单号": "W20250101", "制单日期": "2025-01-01"},
              "date_check": {"actual": "2025-01-02"},
              "field_metadata": {"运单号": {"low_confidence": True}}}
    project_fields(result)
    assert tuple(result["fields"]) == OUTPUT_FIELDS
    assert result["fields"]["签收日期"] == "2025-01-02"
    assert result["fields"]["实收数量"] == result["fields"]["拒收数量"] == ""
    assert result["field_metadata"] == {}
    assert recognition_fields(result)["制单日期"] == "2025-01-01"
    project_fields(result)
    assert recognition_fields(result)["运单号"] == "W20250101"


def test_printed_contact_is_not_handwritten_receiver():
    rows = [row("发货单位：三星", y=.35), row("张三 13812345678", y=.32),
            row("仓库接收人：李四", y=.8)]
    assert printed_extras(rows)["仓库联系人"] == "张三"
    assert handwritten_candidates(rows) == {"仓库接收人": "李四"}


def test_contact_never_uses_receipt_address_or_signature_as_fallback():
    rows = [row("发货单位：三星", y=.35), row("收货地址：广东省", y=.32), row("仓库接收人：李四", y=.8)]
    assert printed_extras(rows)["仓库联系人"] == ""


def test_total_uses_quantity_column_not_weight_or_number_of_rows():
    rows = [row("合计：", x=.05), row("12", x=.60, width=.04), row("18", x=.70, width=.04)]
    assert printed_extras(rows)["合计数量"] == "12"
    assert printed_extras(rows[:1])["合计数量"] == ""


def test_quantities_are_never_recognized_locally_even_with_clear_text():
    rows = [row("实收数量：123"), row("拒收数量：0", y=.6), row("仓库接收人：张三 25年1月2日", y=.8)]
    context = SimpleNamespace(page=lambda _: rows, document_type={"type": "receipt"})
    result = stage_handwriting.execute(context, SimpleNamespace(route={"page": "vision"}))
    assert result["handwriting_fields"] == {"仓库接收人": "张三"}
    assert result["handwriting_metadata"]["仓库接收人"]["low_confidence"]


def test_date_or_empty_label_never_becomes_signature():
    assert handwritten_candidates([row("仓库接收人：2025-01-02")]) == {"仓库接收人": ""}
    assert handwritten_candidates([row("仓库接收人："), row("备注：李四", y=.8)]) == {"仓库接收人": ""}


def test_human_date_edit_updates_export_field():
    from app import _apply_human_edits
    result = _apply_human_edits({"fields": {"要求到货": "2025-01-02", "签章要求": "测试有限公司", "制单日期": "2025-01-01"}},
                                {"actual_date": "2025-01-02", "seal_text": "测试有限公司"})
    assert result["fields"]["签收日期"] == result["date_check"]["actual"] == "2025-01-02"
    assert tuple(result["fields"]) == OUTPUT_FIELDS
    assert result["internal_fields"]["制单日期"] == "2025-01-01"


def test_new_truth_does_not_require_removed_fields_or_optional_products():
    from receipt_ocr.evaluation import build_ground_truth_entry
    result = project_fields({"fields": {"客户名称": "客户", "要求到货": "2025-01-02", "签章要求": "客户章"},
                             "date_check": {"actual": "2025-01-02"}})
    truth = build_ground_truth_entry(result, seal_should_match=True)
    assert set(truth["fields"]) == set(OUTPUT_FIELDS)
    assert truth["fields"]["实收数量"] == ""
    assert truth["product_rows"] == []


def test_review_groups_and_plan_keep_handwriting_separate():
    import app as web
    from bs4 import BeautifulSoup
    html = BeautifulSoup(web.app.test_client().get("/").data, "html.parser")
    assert html.select_one('[data-target="handwriting"]')
    assert html.select_one('[data-review-panel="handwriting"] [data-handwritten-fields]')
    assert not html.select_one('[data-review-panel="fields"] [name="actual_date"]')
    assert html.select_one('[data-target="products"]')
