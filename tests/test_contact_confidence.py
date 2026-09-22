import pytest

from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.parser import estimate_field_confidences
from receipt_ocr.requested_fields import printed_extras


def row(text, confidence=.98, y=.32):
    return TextObservation(text, confidence, .1, y, .7, .02)


def metadata(text, confidence=.98, value=None, extra_rows=()):
    rows = [row(text, confidence), row("发货单位：广州速达", y=.35), *extra_rows]
    fields = printed_extras(rows)
    if value is not None:
        fields["仓库联系人"] = value
    return estimate_field_confidences(fields, rows)["仓库联系人"]


def test_multiple_contacts_retain_ocr_row_score_instead_of_default_42():
    result = metadata("于春梅15508666378/王红鑫13853187051")
    assert result["value"] == "于春梅、王红鑫"
    assert result["confidence"] == .98
    assert not result["low_confidence"]
    assert [item["name"] for item in result["candidates"]] == ["于春梅", "王红鑫"]
    assert all(item["ocr_confidence"] == .98 for item in result["candidates"])
    assert all(item["evidence_text"] == "于春梅15508666378/王红鑫13853187051"
               for item in result["candidates"])


@pytest.mark.parametrize("text", [
    "张三13812345678",
    "张三13812345678/13912345678/010-12345678",
    "联系人：张三",
])
def test_phone_length_and_contact_label_do_not_reduce_score(text):
    assert metadata(text, .93)["confidence"] == .93


def test_low_quality_ocr_is_not_boosted():
    result = metadata("于春梅15508666378/王红鑫13853187051", .51)
    assert result["confidence"] == .51
    assert result["low_confidence"]


def test_incomplete_name_controls_field_warning_without_changing_value():
    result = metadata("座机0755-29042475/徐华送13421366591/张15999596126")
    assert result["value"] == "徐华送、张"
    assert result["confidence"] == .68
    assert result["low_confidence"]
    assert result["candidates"][0]["confidence"] == .98
    assert result["candidates"][1]["reason"] == "姓名不完整"


def test_missing_candidate_evidence_is_not_hidden_by_a_recognized_name():
    result = metadata("张三13812345678", value="张三、李四")
    assert result["confidence"] == 0
    assert result["low_confidence"]
    assert result["candidates"][1]["ocr_confidence"] is None


def test_unrelated_high_confidence_name_does_not_override_source_row():
    result = metadata("张三13812345678", .49,
                      extra_rows=[row("仓库接收人：张三", 1.0, y=.8)])
    assert result["confidence"] == .49


def test_empty_contact_has_no_confidence():
    result = metadata("座机0755-29042475")
    assert result["value"] == ""
    assert result["confidence"] == 0
    assert result["candidates"] == []


def test_other_fields_keep_existing_confidence_policy():
    result = estimate_field_confidences({"客户名称": "测试有限公司"},
                                       [row("测试有限公司", .97)])
    assert result["客户名称"]["confidence"] == .97
