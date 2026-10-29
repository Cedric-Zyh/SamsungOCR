from copy import deepcopy

import pytest

from receipt_ocr.domain.fields.schema import PRINTED_FIELDS
from receipt_ocr.domain.printed_field_selection import canonical_printed_field, select_printed_fields


def variant(method, fields, metadata=None, **extra):
    return {"method": method, "result": {"fields": fields,
            "field_metadata": metadata or {}, **extra}}


def saved(live):
    return {"method": live["method"], "error": live.get("error"),
            "value": deepcopy(live["result"]["fields"]), "details": deepcopy(live["result"])}


@pytest.mark.parametrize("convert", [lambda values: values, lambda values: [saved(v) for v in values]])
def test_fields_are_selected_independently_and_preserve_each_source(convert):
    local = variant("paddle_v6", {"客户名称": "客户甲", "要求到货": "", "合计数量": "8", "仓库接收人": "不属于印刷字段"},
                    {"客户名称": {"source": "Paddle", "confidence": .96}})
    remote = variant("danzhengtong", {"客户名称": "", "要求到货": "2025-01-04", "合计数量": "8"},
                     {"要求到货": {"source": "单证通", "simulated": False, "low_confidence": False, "provider_ratio": 1}})
    variants = convert([local, remote])
    original = deepcopy(variants)
    output = select_printed_fields(variants)
    assert set(output["fields"]) == set(PRINTED_FIELDS)
    assert output["fields"]["客户名称"] == "客户甲"
    assert output["fields"]["要求到货"] == "2025-01-04"
    assert output["metadata"]["客户名称"]["source"] == "Paddle"
    assert output["metadata"]["要求到货"]["source"] == "单证通"
    assert output["selections"]["要求到货"]["selected_method"] == "danzhengtong"
    assert "confidence" not in output["metadata"]["要求到货"]
    assert not output["conflicts"]
    output["metadata"]["要求到货"]["source"] = "changed"
    assert variants == original


def test_screenshot_record_differences_in_width_missing_and_empty_values_are_not_conflicts():
    local = variant("paddle_v6", {"仓库联系人": "退换机", "签章要求": "京小服科技服务有限公司维修中心专用章（04）"},
                    {"仓库联系人": {"confidence": .988}})
    remote = variant("danzhengtong", {"仓库联系人": "", "实收数量": "", "拒收数量": "",
                                       "签章要求": "京小服科技服务有限公司维修中心专用章(04)"})
    output = select_printed_fields([local, remote])
    assert not output["conflicts"]
    assert output["fields"]["仓库联系人"] == "退换机"
    assert output["fields"]["签章要求"] == local["result"]["fields"]["签章要求"]
    assert output["selections"]["实收数量"]["selected_method"] is None


@pytest.mark.parametrize("name,first,second", [
    ("要求到货", "2025年1月4日", "2025-01-04"),
    ("要求到货", "2025 / 1 / 4", "2025.01.04"),
    ("合计数量", "1,000.00", "1000"),
    ("实收数量", 0, "0.0"),
    ("拒收数量", "０", "0"),
    ("客户名称", "客户 甲", "客户甲"),
])
def test_equivalent_formats_compare_equal_without_changing_raw_evidence(name, first, second):
    variants = [variant("one", {name: first}), variant("two", {name: second})]
    original = deepcopy(variants)
    output = select_printed_fields(variants)
    assert not output["conflicts"]
    assert output["fields"][name] != ""
    assert variants == original


def test_meaningful_seal_symbols_are_not_removed_by_normalization():
    assert canonical_printed_field("签章要求", "收货章①") != canonical_printed_field("签章要求", "收货章1")
    assert canonical_printed_field("签章要求", "收货章(04)") != canonical_printed_field("签章要求", "收货章04")


@pytest.mark.parametrize("name,bad,good", [
    ("要求到货", "2025-02-30", "2025-02-28"),
    ("要求到货", "2025-01", "2025-01-04"),
    ("实收数量", "O", "0"),
    ("合计数量", "1,00", "100"),
    ("拒收数量", "-1", "1"),
])
def test_valid_field_shape_wins_over_invalid_high_confidence_candidate(name, bad, good):
    variants = [variant("bad", {name: bad}, {name: {"confidence": .99}}),
                variant("valid", {name: good}, {name: {"confidence": .6, "low_confidence": True}})]
    output = select_printed_fields(variants)
    assert output["fields"][name] == good
    assert output["selections"][name]["selected_method"] == "valid"
    assert output["metadata"][name]["low_confidence"] is True
    assert not output["conflicts"]


def test_unrecognized_shape_remains_visible_but_not_marked_as_confident():
    output = select_printed_fields([variant("ocr", {"要求到货": "2025-02-30"},
                                            {"要求到货": {"confidence": .99, "low_confidence": False}})])
    assert output["fields"]["要求到货"] == "2025-02-30"
    assert output["metadata"]["要求到货"]["low_confidence"] is True
    assert "格式需核对" in output["selections"]["要求到货"]["reason"]


@pytest.mark.parametrize("invalid", ["error", "pending", "simulated", "failed_stage", "failed_trace"])
def test_unusable_provider_evidence_cannot_overrule_real_field(invalid):
    broken = variant("broken", {"客户名称": "错误客户"}, {"客户名称": {"confidence": .99}})
    if invalid == "error":
        broken["error"] = "识别失败"
    elif invalid in {"pending", "simulated"}:
        broken["result"]["field_metadata"]["客户名称"][invalid] = True
    elif invalid == "failed_stage":
        broken["result"]["recognition_status"] = {"fields": "识别失败"}
    else:
        broken["result"]["danzhengtong"] = {"mode": "real", "status": "failed"}
    output = select_printed_fields([saved(broken), saved(variant("real", {"客户名称": "客户甲"}))])
    assert output["fields"]["客户名称"] == "客户甲"
    assert not output["conflicts"]


def test_stronger_field_wins_and_only_unresolved_fields_report_conflict():
    first = variant("first", {"客户名称": "客户甲", "签章要求": "客户甲收货章"}, {
        "客户名称": {"confidence": .5, "low_confidence": True}, "签章要求": {"confidence": .91}})
    second = variant("second", {"客户名称": "客户乙", "签章要求": "客户乙收货章"}, {
        "客户名称": {"confidence": .95}, "签章要求": {"confidence": .93}})
    output = select_printed_fields([first, second])
    assert output["fields"]["客户名称"] == "客户乙"
    assert output["fields"]["签章要求"] == "客户乙收货章"
    assert output["conflicts"] == ["签章要求"]


def test_selected_fallback_belongs_to_the_same_provider_and_value():
    first = variant("first", {"客户名称": "客户甲"}, {"客户名称": {"confidence": .5, "low_confidence": True}},
                    field_fallbacks={"客户名称": {"value": "客户甲", "source": "旧来源"}})
    second = variant("second", {"客户名称": "客户乙"}, {"客户名称": {"confidence": .95}},
                     field_fallbacks={"客户名称": {"value": "客户乙", "source": "续页", "position": [1, 2]}})
    original = deepcopy(second)
    output = select_printed_fields([first, second])
    assert output["field_fallbacks"]["客户名称"]["source"] == "续页"
    output["field_fallbacks"]["客户名称"]["position"].append(3)
    assert second == original


def test_mismatched_metadata_cannot_lend_confidence_to_a_different_value():
    first = variant("first", {"客户名称": "客户甲"}, {"客户名称": {"value": "别的客户", "confidence": .99}})
    second = variant("second", {"客户名称": "客户乙"}, {"客户名称": {"confidence": .94}})
    output = select_printed_fields([first, second])
    assert output["fields"]["客户名称"] == "客户乙"
    assert output["conflicts"] == ["客户名称"]


def test_provider_specific_ratio_does_not_decide_between_different_values():
    output = select_printed_fields([
        variant("first", {"客户名称": "客户甲"}, {"客户名称": {"provider_ratio": 1, "low_confidence": False}}),
        variant("second", {"客户名称": "客户乙"}, {"客户名称": {"provider_ratio": .1, "low_confidence": False}}),
    ])
    assert output["conflicts"] == ["客户名称"]
    assert "confidence" not in output["metadata"]["客户名称"]
