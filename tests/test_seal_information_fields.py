import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.seal_standards import standard_comparison
from receipt_ocr.runtime.safety import _apply_single_paddle_safety


NAME = "三星授权（成都市欣金维电子）服务中心"
REQUIRED = NAME + "授权代码:4910964"
ADDRESS = "成都市大升南路173号附4-5号（儿童医院往北200米）"
CONTACT = f"地址：{ADDRESS}\n电话：028-85438139"
STAMP = NAME + "\n授权代码：4910964\n" + CONTACT


def compare(required=REQUIRED, text=STAMP):
    return compare_seal_text_strict(required, [text], mismatch_over_partial=True)


def test_full_information_stamp_matches_required_name_and_code():
    result = compare()
    assert result["status"] == "匹配"
    assert result["reliable"] is True
    assert result["score"] == result["requirement_coverage"] == 1
    assert result["recognized"] == STAMP
    assert result["all_recognized"] == [STAMP]
    assert result["comparison_policy"] == "strict_information_fields"
    assert result["field_comparison"]["matched_fields"] == ["名称", "授权代码"]
    assert result["field_comparison"]["additional_fields"] == ["地址", "电话"]
    assert "附加信息" in result["message"]


@pytest.mark.parametrize("text", [
    STAMP.replace("：", ":").replace("\n", ""),
    STAMP.replace("4910964", "４９１０９６４"),
    STAMP.replace("授权代码：", "授权代码"),
    NAME + "\n电话:028-85438139\n授权代码:4910964\n地址:" + ADDRESS,
])
def test_typography_and_explicit_field_order_do_not_change_identity(text):
    assert compare(text=text)["status"] == "匹配"


@pytest.mark.parametrize("text", [
    STAMP.replace("欣金维", "欣金雄"),
    STAMP.replace("服务中心", "服务中心分部"),
    STAMP.replace("4910964", "4910965"),
    STAMP.replace("4910964", "491096"),
    STAMP.replace("4910964", "49109640"),
    STAMP.replace("4910964", "491O964"),
    STAMP.replace("授权代码：4910964\n", ""),
    STAMP.replace("授权代码：", "授权代号："),
    STAMP + "\n授权代码:4910965",
    STAMP + "\n电话:028-00000000",
    STAMP + "\n其他公司收货章",
    STAMP.replace("地址：", ""),
    STAMP.replace("电话：028-85438139", "电话："),
    NAME + "授权代码:4910964其他信息",
    NAME + "授权代码:4910964站点:另一个站点",
])
def test_conflicting_incomplete_or_ambiguous_fields_never_pass(text):
    assert compare(text=text)["status"] != "匹配"


@pytest.mark.parametrize("field,value,wrong", [
    ("地址", ADDRESS, ADDRESS.replace("173", "174")),
    ("电话", "028-85438139", "028-85438138"),
])
def test_contact_field_is_mandatory_when_requirement_mentions_it(field, value, wrong):
    required = REQUIRED + f"{field}:{value}"
    assert compare(required)["status"] == "匹配"
    assert compare(required, STAMP.replace(value, wrong))["status"] != "匹配"
    assert compare(required, REQUIRED)["status"] != "匹配"


def test_plain_name_can_ignore_contact_but_not_an_extra_identity_code():
    assert compare(NAME, NAME + CONTACT)["status"] == "匹配"
    assert compare(NAME, STAMP)["status"] != "匹配"
    assert compare(NAME, NAME + "分公司" + CONTACT)["status"] != "匹配"


def test_separate_stamp_candidates_cannot_supply_each_others_fields():
    result = compare_seal_text_strict(REQUIRED, [NAME, "授权代码:4910964" + CONTACT])
    assert result["status"] != "匹配"


def test_matching_stamp_is_selected_without_discarding_original_evidence():
    wrong = STAMP.replace("4910964", "4910965")
    result = compare_seal_text_strict(REQUIRED, [wrong, STAMP], mismatch_over_partial=True)
    assert result["status"] == "匹配"
    assert result["recognized"] == STAMP
    assert result["all_recognized"] == [wrong, STAMP]


def test_standard_still_requires_matching_shape_and_exposes_match_reason():
    receipt = {
        "fields": {"签章要求": REQUIRED},
        "processing_artifacts": {"seals": [{
            "index": 0, "shape_code": "rectangle", "color": "red",
            "original_url": "/files/artifacts/stamp.png",
            "color_isolated_url": "/files/artifacts/clean.png",
            "rectangular_text": STAMP,
        }]},
    }
    missing = standard_comparison(receipt, None)
    assert missing["status"] == "missing"
    assert missing["candidates"][0]["text_status"] == "匹配"
    assert "名称、授权代码一致" in missing["candidates"][0]["text_match_message"]
    assert standard_comparison(receipt, {"shape": "round", "color": "red"})["status"] == "unmatched"
    assert standard_comparison(receipt, {"shape": "rectangle", "color": "red"})["status"] == "matched"


@pytest.mark.parametrize("text,simulated,reliable", [
    (STAMP, False, True),
    (STAMP.replace("4910964", "4910965"), False, False),
    (STAMP.replace("欣金维", "欣金雄"), False, False),
    (STAMP, True, False),
])
def test_single_model_policy_rechecks_required_fields(text, simulated, reliable):
    check = compare()
    # Retain the previous match metadata to test that original text is rechecked.
    check.update(recognized=text, simulated=simulated)
    _apply_single_paddle_safety(
        {}, check, {"page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_v6"}
    )
    assert check["reliable"] is reliable
