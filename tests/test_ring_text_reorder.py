import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.postprocess.ring_text import reorder_ring_text


def test_reorders_company_text_cut_at_circular_seam():
    result = reorder_ring_text(
        "公司济南新宇航科技发展有限",
        "济南新宇航科技发展有限公司",
    )
    assert result["raw_text"] == "公司济南新宇航科技发展有限"
    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["changed"] is True
    assert result["seam_offset"] == 2
    assert result["reason"] == "exact_requirement_cyclic_reorder"


def test_company_suffix_fallback_still_works_without_a_requirement():
    result = reorder_ring_text("公司济南新宇航科技发展有限")

    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["reason"] == "company_suffix_cyclic_reorder"


@pytest.mark.parametrize("offset", range(len("太原市伊加壹电子服务总汇")))
def test_aligns_every_seam_without_a_company_suffix(offset):
    expected = "太原市伊加壹电子服务总汇"
    raw = expected[offset:] + expected[:offset]

    result = reorder_ring_text(raw, expected)

    assert result["raw_text"] == raw
    assert result["source"] == raw
    assert result["text"] == expected
    assert result["seam_offset"] == (len(expected) - offset) % len(expected)
    assert result["changed"] is (offset != 0)
    assert compare_seal_text_strict(expected, [raw, result["text"]])["status"] == "匹配"


@pytest.mark.parametrize("expected,raw", [
    ("太原市伊加壹电子服务总汇", "原市伊服壹电子服务总汇太"),  # wrong glyph
    ("太原市伊加壹电子服务总汇", "原市伊加壹电子服务汇太"),  # missing glyph
    ("太原市伊加壹电子服务总汇", "原市伊加壹电子服务总汇太太"),  # extra glyph
    ("太原市伊加壹电子服务总汇", "原市伊壹加电子服务总汇太"),  # same counts, wrong order
    ("甲乙甲丙", "乙丙乙甲"),  # same set and length, different counts
    ("甲乙甲丙", "甲甲乙丙"),  # same counts, not a cyclic rotation
    ("客户1收货章", "收货章客户①"),
    ("客户A收货章", "收货章客户a"),
    ("客户收货章", "收货章客户!"),
])
def test_character_errors_and_non_cyclic_permutations_do_not_match(expected, raw):
    result = reorder_ring_text(raw, expected)

    assert result["text"] == raw
    assert result["changed"] is False
    assert compare_seal_text_strict(expected, [raw, result["text"]])["status"] != "匹配"


def test_repeated_characters_are_preserved_when_aligning():
    result = reorder_ring_text("甲丙甲乙", "甲乙甲丙")

    assert result["text"] == "甲乙甲丙"
    assert result["seam_offset"] == 2


def test_exact_alignment_uses_the_existing_strict_typography_rules():
    result = reorder_ring_text("收货章 客户（Ａ１）", "客户(A1)收货章")

    assert result["raw_text"] == "收货章客户A1"
    assert result["text"] == "客户A1收货章"
    assert result["seam_offset"] == 3


def test_exact_alignment_takes_priority_over_suffix_heuristics():
    result = reorder_ring_text("经营部太原市公司用品", "太原市公司用品经营部")

    assert result["text"] == "太原市公司用品经营部"
    assert result["reason"] == "exact_requirement_cyclic_reorder"


def test_alternative_must_independently_contain_the_complete_ring():
    result = reorder_ring_text(
        "原市伊服壹电子服务总汇太",
        "太原市伊加壹电子服务总汇",
        alternatives=["总汇太原市伊加壹电子服务"],
    )

    assert result["raw_text"] == "原市伊服壹电子服务总汇太"
    assert result["source"] == "总汇太原市伊加壹电子服务"
    assert result["text"] == "太原市伊加壹电子服务总汇"
    assert result["seam_offset"] == 2


@pytest.mark.parametrize("raw,expected", [("", "太原市伊加壹电子服务总汇"), ("原市伊加壹电子服务总汇太", "")])
def test_missing_reading_or_requirement_does_not_trigger_exact_alignment(raw, expected):
    result = reorder_ring_text(raw, expected)

    assert result["text"] == raw
    assert result["changed"] is False


def test_keeps_complete_ring_text_and_does_not_invent_requirement_characters():
    result = reorder_ring_text(
        "济南新宇航科技发展有限公司",
        "北京另一家公司有限公司",
    )
    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["changed"] is False


def test_can_use_alternative_observation_as_an_additional_ring_candidate():
    result = reorder_ring_text(
        "公司济南新宇航科技发展有限",
        "济南新宇航科技发展有限公司",
        alternatives=["济南新宇航科技发展有限公司"],
    )
    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["source"] in {
        "公司济南新宇航科技发展有限",
        "济南新宇航科技发展有限公司",
    }


def test_fuzhou_warehouse_ring_keeps_its_correct_order_with_centre_requirement():
    ring = "深圳欧瑞特供应链管理有限公司福州仓库"
    result = reorder_ring_text(ring, ring + "收货专用章（1）")

    assert result["raw_text"] == result["text"] == ring
    assert result["changed"] is False
    assert result["seam_offset"] == 0
    assert result["reason"] == "kept_raw_order"
    assert compare_seal_text_strict(ring + "收货专用章(1)", [
        result["text"] + "收货专用章(1)",
    ])["status"] == "匹配"


@pytest.mark.parametrize("offset", range(len("深圳欧瑞特供应链管理有限公司福州仓库")))
def test_warehouse_ring_aligns_every_seam_without_copying_centre_text(offset):
    ring = "深圳欧瑞特供应链管理有限公司福州仓库"
    raw = ring[offset:] + ring[:offset]
    result = reorder_ring_text(raw, ring + "收货专用章(1)")

    assert result["raw_text"] == raw
    assert result["text"] == ring
    assert result["seam_offset"] == (len(ring) - offset) % len(ring)
    assert result["changed"] is (offset != 0)
    assert sorted(result["text"]) == sorted(raw)


@pytest.mark.parametrize("unit", ["福州仓库", "天津分公司", "广州服务中心"])
def test_correct_unit_suffix_with_missing_glyph_is_not_moved(unit):
    company = "深圳欧瑞特供应链管理有限公司"
    raw = company.replace("供应链", "供应") + unit
    result = reorder_ring_text(raw, company + unit + "收货专用章(1)")

    assert result["text"] == raw
    assert result["changed"] is False
    assert compare_seal_text_strict(company + unit, [result["text"]])["status"] == "部分匹配"


@pytest.mark.parametrize("offset", [0, 4, 14])
def test_warehouse_name_is_not_moved_ahead_of_company_without_requirement(offset):
    ring = "深圳欧瑞特供应链管理有限公司福州仓库"
    raw = ring[offset:] + ring[:offset]
    result = reorder_ring_text(raw)

    assert result["text"] == ring
    assert sorted(result["text"]) == sorted(raw)


def test_wrong_warehouse_name_is_not_repaired_from_requirement():
    company = "深圳欧瑞特供应链管理有限公司"
    raw = company + "泉州仓库"
    result = reorder_ring_text(raw, company + "福州仓库收货专用章(1)")

    assert result["text"] == raw
    assert result["changed"] is False
    assert compare_seal_text_strict(company + "福州仓库", [result["text"]])["status"] == "不匹配"
