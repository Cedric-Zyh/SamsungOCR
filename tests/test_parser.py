from datetime import date

from receipt_ocr.parser import (
    TextObservation,
    compare_dates,
    compare_seal_text,
    enrich_fields,
    estimate_field_confidences,
    find_receipt_date,
    parse_date,
    parse_fields,
    parse_product_table,
    parse_receipt_date,
    estimate_date_confidence,
    repair_contextual_fields,
    standardize_fixed_phrases,
)


def test_parse_date_formats():
    assert parse_date("2025-01-05") == date(2025, 1, 5)
    assert parse_date("2025年 1 月 5 日") == date(2025, 1, 5)
    assert parse_date("2025 / 01 / 05") == date(2025, 1, 5)


def test_parse_fields_recovers_split_required_date_and_excludes_quantity_suffix():
    rows = [
        TextObservation("要求到货：2", 0.95, 0.04, 0.27, 0.09, 0.02),
        TextObservation("2025-01-23", 0.99, 0.12, 0.27, 0.10, 0.02),
        TextObservation("签章要求：三星电子服务中心取机专用章5785258站", 0.98, 0.04, 0.47, 0.37, 0.02),
        TextObservation("实收数量：", 0.97, 0.60, 0.47, 0.08, 0.02),
        TextObservation("（台）", 0.97, 0.69, 0.47, 0.03, 0.02),
    ]

    fields = parse_fields(rows)

    assert fields["要求到货"] == "2025-01-23"
    assert fields["签章要求"] == "三星电子服务中心取机专用章5785258站"


def test_parse_fields_ignores_detached_colon_and_common_label_ocr_errors():
    rows = [
        TextObservation("共应商：", .98, .02, .15, .06, .02),
        TextObservation("中外运物流有限公司", .99, .10, .15, .18, .02),
        TextObservation("客户名称：", .98, .02, .18, .07, .02),
        TextObservation(":", .60, .09, .18, .01, .02),
        TextObservation("青岛和沃电子技术有限公司黄岛分公司", .99, .11, .18, .30, .02),
        TextObservation("消售订单号：0305074401", .99, .02, .24, .20, .02),
    ]

    fields = parse_fields(rows)

    assert fields["供应商"] == "中外运物流有限公司"
    assert fields["客户名称"] == "青岛和沃电子技术有限公司黄岛分公司"
    assert fields["销售订单号"] == "0305074401"


def test_parse_fields_uses_reviewed_receipt_address_label_alias():
    rows = [
        TextObservation("收le址：", .59, .03, .292, .09, .013),
        TextObservation(
            "浙江省温州市鹿城区人民东路109号温州市130",
            .99, .112, .292, .34, .013,
        ),
    ]

    assert parse_fields(rows)["收货地址"] == "浙江省温州市鹿城区人民东路109号温州市130"


def test_parse_fields_stops_long_customer_at_business_code_suffix():
    rows = [
        TextObservation(
            "客户名称：合肥佳元电子通讯产品技术服务有限公司第一分公司So1dToCode:0008374451",
            .99, .04, .18, .60, .02,
        ),
        TextObservation(
            "客户仓库：合肥佳元电子通讯产品技术服务有限公司第一分公司ShipToCode:0008374451",
            .99, .04, .22, .60, .02,
        ),
    ]

    fields = parse_fields(rows)

    assert fields["客户名称"] == "合肥佳元电子通讯产品技术服务有限公司第一分公司"
    assert fields["客户仓库"] == "合肥佳元电子通讯产品技术服务有限公司第一分公司"


def test_short_literal_stamp_substring_is_not_a_perfect_match():
    result = compare_seal_text(
        "三星电子服务中心取机专用章5785258站",
        ["5785258站"],
    )

    assert result["score"] < 0.72
    assert result["reliable"] is False


def test_seal_prefers_same_region_evidence_covering_company_and_stamp_type():
    requirement = "北京京凌科技有限公司海淀第三分公司三星售后服务专用章"
    company_only = "北京京凌科技有限公"
    combined = "三星售后服务专用章北京京凌科技有限公司海淀第三分公司少量噪声"

    result = compare_seal_text(requirement, [company_only, combined])

    assert result["recognized"] == combined
    assert result["reliable"] is True


def test_business_acceptance_text_satisfies_business_acceptance_stamp_type():
    requirement = "郑州广利达电子技术有限公司业务受理专用章"
    recognized = "郑州广利达电子技术有限公司业务受理"

    result = compare_seal_text(requirement, [recognized])

    assert result["status"] == "匹配"
    assert result["reliable"] is True

    numbered_requirement = "洛阳东利通信有限公司业务受理（2）"
    assert compare_seal_text(
        numbered_requirement,
        ["洛阳东利通信有限公司业务受理"],
    )["reliable"] is False
    assert compare_seal_text(
        numbered_requirement,
        ["洛阳东利通信有限公司业务受理（2）"],
    )["reliable"] is True


def test_date_comparison():
    assert compare_dates("2025-01-05", date(2025, 1, 5))["status"] == "匹配"
    result = compare_dates("2025-01-05", date(2025, 1, 7))
    assert result["status"] == "不匹配"
    assert "晚 2 天" in result["message"]


def test_receipt_date_repairs_common_stamp_overlap_errors():
    required = date(2025, 1, 5)
    assert parse_receipt_date("2054|月5可", required) == required
    assert parse_receipt_date("1月5T", required) == required
    assert parse_receipt_date("202年2月11", date(2025, 2, 11)) == date(2025, 2, 11)
    assert parse_receipt_date("20年月8日", date(2025, 5, 9)) == date(2025, 5, 8)
    assert parse_receipt_date("2035年1月31日", required) is None
    assert parse_receipt_date("1105年2月23日", date(2025, 2, 23)) is None


def test_receipt_date_aggregates_variants_before_accepting_high_confidence_misread():
    rows = [
        TextObservation("2025年1月2日", 0.99, 0.80, 0.55, 0.12, 0.02),
        TextObservation("25年1月12日", 0.80, 0.79, 0.55, 0.13, 0.02),
        TextObservation("2025年1月12日", 0.78, 0.78, 0.56, 0.14, 0.02),
    ]
    found, _ = find_receipt_date(rows, "2025-01-12")
    assert found == date(2025, 1, 12)


def test_receipt_date_does_not_invent_required_date_without_ocr_evidence():
    rows = [TextObservation("2025年1月13日", 0.95, 0.80, 0.55, 0.12, 0.02)]
    found, _ = find_receipt_date(rows, "2025-01-12")
    assert found == date(2025, 1, 13)


def test_truncated_two_digit_receipt_day_cannot_become_reliable_mismatch():
    rows = [
        TextObservation("202年2月2日", .99, .82, .61, .14, .02),
        TextObservation("202年2月2日", .98, .82, .61, .14, .02),
    ]
    actual = date(2025, 2, 2)
    assert estimate_date_confidence(rows, "2025-02-21", actual) < .72


def test_one_month_day_fragment_equal_to_requirement_is_not_reliable():
    rows = [
        TextObservation("4月25日", .99, .82, .61, .14, .02),
    ]
    actual = date(2025, 4, 25)
    assert estimate_date_confidence(rows, "2025-04-25", actual) < .72


def test_seal_text_tolerates_one_ocr_character_error():
    result = compare_seal_text(
        "京小服科技服务有限公司维修中心专用章（04）",
        ["卜服科技服务有限公司维修中心专用章（04）"],
    )
    assert result["status"] == "匹配"
    assert result["score"] >= 0.72


def test_business_stamp_accepts_only_terminal_stamp_glyph_clipped_with_exact_company():
    requirement = "济南新宇航科技发展有限公司业务专用章"

    matched = compare_seal_text(
        requirement,
        ["济南新宇航科技发展有限公司业务专用"],
    )
    company_only = compare_seal_text(
        requirement,
        ["济南新宇航科技发展有限公司"],
    )
    type_only = compare_seal_text(requirement, ["业务专用"])

    assert matched["status"] == "匹配"
    assert matched["reliable"] is True
    assert company_only["reliable"] is False
    assert type_only["reliable"] is False


def test_circular_seal_can_recover_reversed_place_prefix_with_matching_core():
    result = compare_seal_text(
        "南昌永航科技有限公司",
        ["昌南四月华昌水技有惠為水航科技有限公司永航科技有服公司"],
    )

    assert result["status"] == "匹配"
    assert result["reliable"] is True
    assert result["company_score"] >= .88


def test_reversed_place_prefix_alone_cannot_match_another_company():
    result = compare_seal_text(
        "南昌永航科技有限公司",
        ["昌南完全不同商贸有限公司"],
    )

    assert result["reliable"] is False


def test_generic_suffix_overlap_cannot_hide_a_different_company_name():
    noisy = (
        "拒收数量收货客户售后专用章医信设备有限公司"
        "国大连华设备有限公司大连福信贸备有限公司"
    )
    result = compare_seal_text(
        "大连北华通信设备有限公司售后专用章",
        [noisy],
    )

    assert result["status"] == "无法判断"
    assert result["reliable"] is False
    assert result["company_score"] < .78


def test_shorter_complete_different_company_name_forces_human_review():
    result = compare_seal_text(
        "十堰市万盛达通讯器材有限公司",
        ["十堰盛瑞通讯器材有限公司"],
    )

    assert result["company_conflict"] is True
    assert result["status"] == "无法判断"
    assert result["reliable"] is False


def test_company_only_seal_accepts_strong_core_and_legal_marker_at_safe_boundary():
    result = compare_seal_text(
        "南昌永航科技有限公司",
        ["收货客户盖章，限公司205年月11塑1500南昌永航科"],
    )

    assert result["status"] == "匹配"
    assert result["reliable"] is True
    assert result["score"] >= .72
    assert result["company_score"] >= .82


def test_short_stamp_requirement_is_low_confidence_even_when_glyphs_are_clear():
    rows = [TextObservation("贵小", 0.99, 0.12, 0.48, 0.03, 0.01)]
    metadata = estimate_field_confidences({"签章要求": "贵小"}, rows)
    assert metadata["签章要求"]["confidence"] == 0.45
    assert metadata["签章要求"]["low_confidence"] is True
    assert "内容过短" in metadata["签章要求"]["source"]


def test_fixed_receipt_note_is_standardized_but_raw_ocr_is_retained():
    fields = {"签收说明": "如未签实收数量视为整单完整签改"}
    corrections = standardize_fixed_phrases(fields)
    assert fields["签收说明"] == "如未签实收数量视为整单完整签收"
    assert corrections["签收说明"]["original"] == "如未签实收数量视为整单完整签改"
    assert corrections["签收说明"]["confidence"] >= 0.9


def test_known_repeated_signature_requirement_repairs_one_missing_glyph_only():
    fields = {"签章要求": "京服科技服务有限公司维修中心专用章（04）"}
    corrections = standardize_fixed_phrases(fields)
    assert fields["签章要求"] == "京小服科技服务有限公司维修中心专用章（04）"
    assert corrections["签章要求"]["original"].startswith("京服")


def test_clipped_fixed_receipt_note_uses_confirmed_template_prefix():
    fields = {"签收说明": "如末"}
    corrections = standardize_fixed_phrases(fields)
    assert fields["签收说明"] == "如未签实收数量视为整单完整签收"
    assert corrections["签收说明"]["confidence"] >= .9


def test_fixed_receipt_note_uses_distinctive_confirmed_template_suffix():
    fields = {"签收说明": "中朱物数量视为整单完整签收"}
    corrections = standardize_fixed_phrases(fields)

    assert fields["签收说明"] == "如未签实收数量视为整单完整签收"
    assert corrections["签收说明"]["original"] == "中朱物数量视为整单完整签收"
    assert corrections["签收说明"]["confidence"] >= .9


def test_customer_warehouse_observed_label_alias_extracts_value_and_ship_to_code():
    rows = [
        TextObservation(
            "客片仓库：上海信威摄影器材有限公司ShipToCode:0006237106",
            .99, .04, .22, .60, .02,
        ),
    ]

    fields = parse_fields(rows)

    assert fields["客户仓库"] == "上海信威摄影器材有限公司"
    assert fields["ShipToCode"] == "0006237106"


def test_fixed_receipt_note_uses_confirmed_template_when_label_value_is_missing():
    rows = [TextObservation("签收说明：", .99, .02, .50, .08, .02)]
    assert parse_fields(rows)["签收说明"] == "如未签实收数量视为整单完整签收"


def test_unrelated_signature_requirement_is_not_forced_to_known_template():
    fields = {"签章要求": "北京三星电子技术服务站维修专用章0004754613"}
    assert standardize_fixed_phrases(fields) == {}
    assert fields["签章要求"].startswith("北京三星")


def test_unrelated_receipt_note_is_not_forced_to_template_phrase():
    fields = {"签收说明": "拒收时请写明原因并联系仓库"}
    assert standardize_fixed_phrases(fields) == {}
    assert fields["签收说明"] == "拒收时请写明原因并联系仓库"


def test_context_repairs_matching_company_prefix_but_never_completes_printed_carrier():
    fields = {
        "承运商": "深圳市快运通物流有限",
        "客户名称": "深圳市星睿奇光电有限公司",
        "签章要求": "深圳市星容奇光电有限公司仓储部收货章",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["承运商"] == "深圳市快运通物流有限"
    assert fields["签章要求"] == "深圳市星睿奇光电有限公司仓储部收货章"
    assert "承运商" not in corrections
    assert corrections["签章要求"]["original"] == "深圳市星容奇光电有限公司仓储部收货章"


def test_signature_requirement_trims_fixed_receipt_note_tail():
    fields = {
        "客户名称": "湖北楚飞网络科技有限公司",
        "签章要求": "三星电子孝感服务中心单完",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "三星电子孝感服务中心"
    assert corrections["签章要求"]["source"] == "签收说明跨行噪声清理"


def test_signature_requirement_trims_reviewed_samsung_center_table_noise():
    fields = {
        "客户名称": "青岛京邦达供应链科技有限公司",
        "签章要求": "三星电子青岛维修中心单定",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "三星电子青岛维修中心"
    assert corrections["签章要求"]["original"] == "三星电子青岛维修中心单定"
    assert corrections["签章要求"]["source"] == "签收说明跨行噪声清理"

    unrelated = {"签章要求": "普通维修中心单定"}
    assert repair_contextual_fields(unrelated) == {}
    assert unrelated["签章要求"] == "普通维修中心单定"


def test_signature_requirement_trims_joined_received_quantity_one():
    fields = {
        "客户名称": "上海凝鹏通讯科技有限公司",
        "签章要求": "上海凝鹏通讯科技有限公司收货专用章壹",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "上海凝鹏通讯科技有限公司收货专用章"
    assert corrections["签章要求"]["original"].endswith("壹")


def test_context_does_not_replace_unrelated_stamp_company():
    fields = {
        "客户名称": "湖北京邦达供应链科技有限公司",
        "签章要求": "京小服科技服务有限公司维修中心专用章（04）",
    }
    assert repair_contextual_fields(fields) == {}


def test_customer_name_uses_fuller_near_identical_warehouse_evidence():
    fields = {
        "客户名称": "州市新六菱电科技有限公司",
        "客户仓库": "广州市新六菱电子科技有限公司",
        "签章要求": "三星电子服务中心取机专用章",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["客户名称"] == "广州市新六菱电子科技有限公司"
    assert corrections["客户名称"]["original"] == "州市新六菱电科技有限公司"
    assert corrections["客户名称"]["source"] == "客户仓库同主体交叉校正"


def test_customer_name_repairs_missing_glyphs_from_repeated_branch_warehouse():
    fields = {
        "客户名称": "合肥佳元电通讯产品技术服务有限公司第分公司",
        "客户仓库": "合肥佳元电子通讯产品技术服务有限公司第一分公司",
        "签章要求": "合肥佳元电子第一分公司机售后专用章",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["客户名称"] == "合肥佳元电子通讯产品技术服务有限公司第一分公司"
    assert fields["签章要求"] == "合肥佳元电子第一分公司手机售后专用章"
    assert corrections["客户名称"]["source"] == "客户仓库同主体交叉校正"


def test_signature_requirement_trims_misread_received_quantity_label():
    fields = {
        "客户名称": "广州中启通信科技有限公司",
        "客户仓库": "广州中启通信科技有限公司",
        "签章要求": "站代码：5988247 02081769324广州中启通信科技有限公司定收数量",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "站代码：598824702081769324广州中启通信科技有限公司"
    assert corrections["签章要求"]["source"] == "签收说明跨行噪声清理"


def test_station_contact_requirement_repairs_only_one_missing_company_prefix():
    fields = {
        "客户名称": "广州中启通信科技有限公司",
        "客户仓库": "广州中启通信科技有限公司",
        "签章要求": "站代码：598824702081769324州中启通信科技有限公司",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == (
        "站代码：598824702081769324广州中启通信科技有限公司"
    )
    assert "站代码联系信息" in corrections["签章要求"]["source"]

    conflict = {
        "客户名称": "广州中启通信科技有限公司",
        "客户仓库": "广州中启通信科技有限公司",
        "签章要求": "站代码：598824702081769324深圳中启通信科技有限公司",
    }
    assert repair_contextual_fields(conflict) == {}
    assert conflict["签章要求"].endswith("深圳中启通信科技有限公司")


def test_customer_name_never_borrows_a_different_warehouse_company():
    fields = {
        "客户名称": "广州甲方电子科技有限公司",
        "客户仓库": "广州完全不同物流仓储有限公司",
        "签章要求": "广州甲方电子科技有限公司收货章",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["客户名称"] == "广州甲方电子科技有限公司"
    assert "客户名称" not in corrections


def test_unusable_customer_name_uses_exact_warehouse_and_requirement_repetition():
    fields = {
        "客户名称": "i",
        "客户仓库": "武汉市飞鸿通信器材有限公司",
        "签章要求": "武汉市飞鸿通信器材有限公司",
    }

    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "武汉市飞鸿通信器材有限公司"
    assert corrections["客户名称"]["original"] == "i"
    assert corrections["客户名称"]["source"] == "客户仓库 + 签章要求双重重复字段校正"


def test_unusable_customer_uses_complete_warehouse_when_business_codes_agree():
    fields = {
        "客户名称": "号",
        "客户仓库": "鄂尔多斯市宏泰恒远商贸有限责任公司",
        "SoldToCode": "0006183342",
        "ShipToCode": "0006183342",
        "签章要求": "三星售后6183342站",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "鄂尔多斯市宏泰恒远商贸有限责任公司"
    assert corrections["客户名称"]["original"] == "号"
    assert "SoldToCode/ShipToCode" in corrections["客户名称"]["source"]

    mismatch = dict(fields, 客户名称="号", ShipToCode="0000000000")
    repair_contextual_fields(mismatch)
    assert mismatch["客户名称"] == "号"


def test_same_complete_customer_and_warehouse_fill_one_missing_business_code():
    company = "辽宁旭睿科技有限公司"
    fields = {
        "客户名称": company,
        "客户仓库": company,
        "ShipToCode": "0004197455",
        "签章要求": f"{company}售后专用章",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["SoldToCode"] == "0004197455"
    assert corrections["SoldToCode"]["original"] == ""
    assert "单侧业务编码" in corrections["SoldToCode"]["source"]

    different = dict(fields, 客户仓库="辽宁另一家公司有限公司", SoldToCode="")
    repair_contextual_fields(different)
    assert different["SoldToCode"] == ""


def test_unusable_customer_name_is_not_repaired_when_repeated_fields_disagree():
    fields = {
        "客户名称": "i",
        "客户仓库": "武汉市飞鸿通信器材有限公司",
        "签章要求": "武汉市另一家公司有限公司",
    }

    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "i"
    assert "客户名称" not in corrections


def test_exact_pickup_station_and_stamp_type_are_reliable_seal_evidence():
    result = compare_seal_text(
        "三星电子服务中心取机专用章（2）6237143站电话：02081061101",
        ["电子服务（2）取机专用章6237143站电话：061101"],
    )
    assert result["status"] == "匹配"
    assert result["reliable"] is True
    assert result["score"] == .94


def test_station_number_alone_cannot_match_pickup_stamp():
    result = compare_seal_text(
        "三星电子服务中心取机专用章（2）6237143站",
        ["普通收货章6237143站"],
    )
    assert result["reliable"] is False


def test_samsung_after_sales_station_requires_exact_identifier():
    requirement = "三星售后6183342站"
    exact = compare_seal_text(requirement, ["三星售后6183342站"])
    assert exact["reliable"] is True

    one_digit_wrong = compare_seal_text(requirement, ["三星售后6183842站"])
    assert one_digit_wrong["score"] >= .78
    assert one_digit_wrong["reliable"] is False

    one_digit_missing = compare_seal_text(requirement, ["三星售后618342站"])
    assert one_digit_missing["reliable"] is False


def test_signature_requirement_joins_same_line_ocr_segments():
    rows = [
        TextObservation("签章要求：", 1, .04, .474, .07, .012),
        TextObservation("三星电子服务中心取机专用章（2）", .8, .112, .474, .25, .012),
        TextObservation("6237143站电话：02081061101", 1, .377, .475, .20, .012),
        TextObservation("实收数量：", 1, .57, .491, .08, .012),
    ]
    assert parse_fields(rows)["签章要求"] == (
        "三星电子服务中心取机专用章（2）6237143站电话：02081061101"
    )


def test_customer_company_strips_ship_to_code_with_ocr_a_for_o():
    rows = [
        TextObservation(
            "客户名称：合肥佳元电子通讯产品技术服务有限公司第一分公司ShipToCade:0008374451",
            .95, .05, .2, .7, .02,
        ),
    ]
    assert parse_fields(rows)["客户名称"] == "合肥佳元电子通讯产品技术服务有限公司第一分公司"


def test_printed_creation_date_is_not_overwritten_by_tracking_date():
    fields = {"运单号": "W20250304-008104", "制单日期": "2025-03-08"}
    assert enrich_fields(fields)["制单日期"] == "2025-03-08"


def test_tracking_date_remains_creation_date_fallback_when_ocr_missing():
    fields = {"运单号": "W20250304-008104"}
    assert enrich_fields(fields)["制单日期"] == "2025-03-04"


def test_contextual_repair_removes_observed_company_line_border_prefixes():
    fields = {
        "客户名称": "：贵州宏羿科技有限公司",
        "客户仓库": "贵州宏羿科技有限公司",
        "签章要求": r"\贵州宏羿科技有限公司",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["客户名称"] == "贵州宏羿科技有限公司"
    assert fields["签章要求"] == "贵州宏羿科技有限公司"
    assert corrections["签章要求"]["source"] == "字段左边界孤立笔画清理"


def test_contextual_repair_removes_lowercase_i_before_chinese_requirement_only():
    fields = {"客户名称": "武汉市飞鸿通信器材有限公司", "签章要求": "i武汉市飞鸿通信器材有限公司"}
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "武汉市飞鸿通信器材有限公司"

    latin = {"客户名称": "IBM中国有限公司", "签章要求": "IBM中国有限公司收货章"}
    repair_contextual_fields(latin)
    assert latin["客户名称"] == "IBM中国有限公司"


def test_contextual_repair_recovers_observed_shipping_unit_leading_character():
    fields = {"发货单位": "州源达仓"}
    corrections = repair_contextual_fields(fields)
    assert fields["发货单位"] == "广州源达仓"
    assert corrections["发货单位"]["original"] == "州源达仓"


def test_contextual_repair_drops_border_glyph_only_before_exact_customer_name():
    fields = {
        "客户名称": "郑州广利达电子技术有限公司",
        "客户仓库": "郑州广利达电子技术有限公司",
        "签章要求": "关郑州广利达电子技术有限公司业务受理",
    }
    repair_contextual_fields(fields)
    # The customer has both reviewed form templates.  Removing a one-glyph
    # border artifact must not invent the longer ``专用章`` suffix when the
    # observed printed field cleanly ends at ``业务受理``.
    assert fields["签章要求"] == "郑州广利达电子技术有限公司业务受理"

    clean_short_template = {
        "客户名称": "郑州广利达电子技术有限公司",
        "客户仓库": "郑州广利达电子技术有限公司",
        "签章要求": "郑州广利达电子技术有限公司业务受理",
    }
    corrections = repair_contextual_fields(clean_short_template)
    assert clean_short_template["签章要求"] == "郑州广利达电子技术有限公司业务受理"
    assert "签章要求" not in corrections

    genuine = {"客户名称": "关山科技有限公司", "签章要求": "关山科技有限公司收货章"}
    repair_contextual_fields(genuine)
    assert genuine["签章要求"] == "关山科技有限公司收货章"


def test_contextual_repair_drops_right_border_glyph_with_exact_customer_name():
    fields = {
        "客户名称": "十堰市万盛达通讯器材有限公司",
        "签章要求": "十堰市万盛达通讯器材有限公司司",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "十堰市万盛达通讯器材有限公司"
    assert corrections["签章要求"]["source"] == "客户名称重复字段右边界校正"


def test_contextual_repair_completes_samsung_service_center_suffix():
    fields = {"签章要求": "三星电子孝感服务中"}
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "三星电子孝感服务中心"

    unrelated = {"签章要求": "华北物流服务中"}
    repair_contextual_fields(unrelated)
    assert unrelated["签章要求"] == "华北物流服务中"

    noisy = {"签章要求": "三星电子孝感服务中单完"}
    repair_contextual_fields(noisy)
    assert noisy["签章要求"] == "三星电子孝感服务中心"

    prefix_repaired = {
        "客户名称": "十堰市万盛达通讯器材有限公司",
        "签章要求": "十堰盛瑞通讯器材有限公司司",
    }
    repair_contextual_fields(prefix_repaired)
    assert prefix_repaired["签章要求"] == "十堰市万盛达通讯器材有限公司"


def test_authorization_requirement_repairs_service_center_only_with_code_structure():
    fields = {
        "签章要求": "三星授权（成都利群通讯有限责任公司）服务中授权代码:4913766"
    }
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == (
        "三星授权（成都利群通讯有限责任公司）服务中心授权代码:4913766"
    )
    assert corrections["签章要求"]["source"] == "三星授权代码固定结构末字缺失校正"

    unrelated = {"签章要求": "华北服务中授权代码:4913766"}
    repair_contextual_fields(unrelated)
    assert unrelated["签章要求"] == "华北服务中授权代码:4913766"


def test_samsung_repair_center_requirement_repairs_missing_center_glyph_with_station():
    fields = {"签章要求": "三星电子维修中2310637"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "三星电子维修中心2310637"
    assert "站号固定结构" in corrections["签章要求"]["source"]

    unrelated = {"签章要求": "普通维修中2310637"}
    repair_contextual_fields(unrelated)
    assert unrelated["签章要求"] == "普通维修中2310637"


def test_pickup_station_master_repairs_same_leading_dropout_in_repeated_customer():
    fields = {
        "客户名称": "州市新六菱电子科技有限公司",
        "客户仓库": "州市新六菱电子科技有限公司",
        "签章要求": "三星电子服务中心取机专用章（2）6237143站电话：02081061101",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "广州市新六菱电子科技有限公司"
    assert fields["客户仓库"] == "广州市新六菱电子科技有限公司"
    assert "取机站编号" in corrections["客户名称"]["source"]

    disagreement = {
        "客户名称": "州市新六菱电子科技有限公司",
        "客户仓库": "广州市另一家公司有限公司",
        "签章要求": "三星电子服务中心取机专用章（2）6237143站",
    }
    repair_contextual_fields(disagreement)
    assert disagreement["客户名称"] == "州市新六菱电子科技有限公司"


def test_ship_to_master_repairs_repeated_rare_customer_glyph_only_with_exact_code():
    fields = {
        "客户名称": "佛山市顺德区宇骤通讯器材有限公司",
        "客户仓库": "佛山市顺德区宇骤通讯器材有限公司",
        "ShipToCode": "0006049067",
        "签章要求": "三星SAMSUNG客户服务中心",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "佛山市顺德区宇骥通讯器材有限公司"
    assert fields["客户仓库"] == "佛山市顺德区宇骥通讯器材有限公司"
    assert corrections["客户名称"]["original"] == "佛山市顺德区宇骤通讯器材有限公司"
    assert "ShipToCode" in corrections["客户名称"]["source"]

    wrong_code = {
        "客户名称": "佛山市顺德区宇骤通讯器材有限公司",
        "客户仓库": "佛山市顺德区宇骤通讯器材有限公司",
        "ShipToCode": "0000000000",
    }
    repair_contextual_fields(wrong_code)
    assert wrong_code["客户名称"] == "佛山市顺德区宇骤通讯器材有限公司"


def test_ship_to_master_repairs_single_missing_warehouse_glyph():
    fields = {
        "客户名称": "杭州松峰电子科技有限公司",
        "客户仓库": "杭州松峰电科技有限公司",
        "ShipToCode": "0003367583",
    }

    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "杭州松峰电子科技有限公司"
    assert fields["客户仓库"] == "杭州松峰电子科技有限公司"
    assert corrections["客户仓库"]["original"] == "杭州松峰电科技有限公司"
    assert corrections["客户仓库"]["confidence"] == 0.98
    assert "ShipToCode" in corrections["客户仓库"]["source"]


def test_ship_to_master_repairs_two_repeated_customer_dropouts_then_requirement():
    fields = {
        "客户名称": "合肥佳元电通讯产品技术服务有限公司第一分公司",
        "客户仓库": "合肥佳元电通讯产品技术服务有限公司第一分公司",
        "ShipToCode": "0008374451",
        "签章要求": "肥佳元电子第一分公司手机售后专用章",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == "合肥佳元电子通讯产品技术服务有限公司第一分公司"
    assert fields["客户仓库"] == "合肥佳元电子通讯产品技术服务有限公司第一分公司"
    assert fields["签章要求"] == "合肥佳元电子第一分公司手机售后专用章"
    assert "ShipToCode" in corrections["客户名称"]["source"]


def test_requirement_uses_repeated_customer_and_explicit_business_stamp_suffix():
    fields = {
        "客户名称": "深圳市天音科技发展有限公司",
        "客户仓库": "深圳市天音科技发展有限公司",
        "签章要求": "深圳市天普果复省展套测业务专用章",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "深圳市天音科技发展有限公司业务专用章"
    assert "双重证据" in corrections["签章要求"]["source"]


def test_exact_customer_prefix_preserves_after_sales_business_stamp_type():
    fields = {
        "客户名称": "北京华康君泰贸易有限公司",
        "客户仓库": "北京华康君泰贸易有限公司",
        "签章要求": "北京华康君泰贸易有限公司售后业务专用章",
    }

    repair_contextual_fields(fields)

    assert fields["签章要求"] == "北京华康君泰贸易有限公司售后业务专用章"


def test_requirement_strips_short_stamp_overlap_after_repeated_customer():
    fields = {
        "客户名称": "南京万泓电子有限公司第二分公司",
        "客户仓库": "南京万泓电子有限公司第二分公司",
        "签章要求": "南京万泓电子有限公司第二分公司南京百",
    }
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "南京万泓电子有限公司第二分公司"


def test_pickup_requirement_uses_exact_reviewed_station_number():
    fields = {"签章要求": "三星电子服务单藏海雅募5785258站业"}
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "三星电子服务中心取机专用章5785258站"


def test_second_reviewed_pickup_station_recovers_full_requirement_and_phone():
    fields = {
        "签章要求": "三星电子服务中取机专用章（2）6237143站电话：02081061101"
    }
    repair_contextual_fields(fields)
    assert fields["签章要求"] == (
        "三星电子服务中心取机专用章（2）6237143站电话：02081061101"
    )


def test_codes_embedded_in_customer_rows_are_extracted_before_suffix_cleanup():
    rows = [
        TextObservation(
            "客户名称：合肥佳元电子有限公司So1dToCode:0008374451",
            1, .04, .19, .50, .02,
        ),
        TextObservation(
            "客户仓库：合肥佳元电子有限公司ShipToCode:0008374451",
            1, .04, .22, .50, .02,
        ),
    ]
    fields = parse_fields(rows)

    assert fields["客户名称"] == "合肥佳元电子有限公司"
    assert fields["客户仓库"] == "合肥佳元电子有限公司"
    assert fields["SoldToCode"] == "0008374451"
    assert fields["ShipToCode"] == "0008374451"


def test_after_sales_stamp_suffix_repairs_missing_use_glyph():
    fields = {"签章要求": "湖北元一凡通信有限公司三星售后专章"}
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "湖北元一凡通信有限公司三星售后专用章"


def test_reviewed_ship_to_code_repairs_one_glyph_station_requirement_error():
    fields = {
        "客户名称": "上海信威摄影器材有限公司",
        "客户仓库": "上海信威摄影器材有限公司",
        "ShipToCode": "0006237106",
        "签章要求": "三星电子服务中心上海信威寮站代码6237106",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "三星电子服务中心上海信威站代码6237106"
    assert corrections["签章要求"]["source"].startswith("ShipToCode 签章主数据")


def test_reviewed_ship_to_code_repairs_beijing_service_center_requirement():
    fields = {
        "客户名称": "北京东润丽达科技有限公司",
        "客户仓库": "北京东润丽达科技有限公司",
        "ShipToCode": "0002310637",
        "签章要求": "三星电维修中2310637",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "三星电子维修中心2310637"
    assert corrections["签章要求"]["confidence"] == 0.99
    assert corrections["签章要求"]["source"].startswith("ShipToCode 签章主数据")


def test_after_sales_service_stamp_trims_short_footer_company_fragment():
    fields = {
        "客户名称": "北京京凌科技有限公司海淀第三分公司",
        "客户仓库": "北京京凌科技有限公司海淀第三分公司",
        "签章要求": "北京京凌科技有限公司海淀第三分公司三星售后服务专用章京限公司发",
    }
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "北京京凌科技有限公司海淀第三分公司三星售后服务专用章"


def test_inspection_stamp_suffix_repairs_missing_use_glyph():
    fields = {"签章要求": "云南邮维科技有限公司检测专章（3）"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "云南邮维科技有限公司检测专用章（3）"
    assert corrections["签章要求"]["original"] == "云南邮维科技有限公司检测专章（3）"

    unrelated = {"签章要求": "质量检测专章程说明"}
    repair_contextual_fields(unrelated)
    assert unrelated["签章要求"] == "质量检测专章程说明"


def test_maintenance_stamp_suffix_repairs_missing_use_glyph_before_phone():
    fields = {"签章要求": "哈尔滨晨光智能科技有限公司维修专章045153608531"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "哈尔滨晨光智能科技有限公司维修专用章045153608531"
    assert "维修专用章" in corrections["签章要求"]["source"]

    unrelated = {"签章要求": "内部维修专章045153608531"}
    repair_contextual_fields(unrelated)
    assert unrelated["签章要求"] == "内部维修专章045153608531"


def test_maintenance_stamp_preserves_clean_no_number_printed_template():
    printed = "杭州索兰科技有限公司维修专章"
    fields = {"签章要求": printed}

    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == printed
    assert "签章要求" not in corrections


def test_signature_requirement_trims_full_receipt_note_and_quantity_tail():
    fields = {"签章要求": "三星售后6183342站整单完整签收收数量"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "三星售后6183342站"
    assert corrections["签章要求"]["source"] == "签收说明跨行噪声清理"


def test_signature_requirement_trims_single_receipt_note_glyph_after_station():
    fields = {"签章要求": "三星售后6183342站整单完"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "三星售后6183342站"
    assert corrections["签章要求"]["original"] == "三星售后6183342站整单完"
    assert corrections["签章要求"]["source"] == "签收说明跨行噪声清理"


def test_signature_requirement_trims_short_receipt_note_fragment_after_station():
    fields = {"签章要求": "三星售后6183342站整单"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "三星售后6183342站"
    assert corrections["签章要求"]["original"] == "三星售后6183342站整单"


def test_oval_code_stamp_requirement_uses_two_repeated_customer_fields():
    customer = "广州市知星通讯器材有限公司"
    fields = {
        "客户名称": customer,
        "客户仓库": customer,
        "签章要求": "物流发展有限器材有限公司（盖椭圆的代码章）",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == customer + "（盖椭圆的代码章）"
    assert "椭圆代码章" in corrections["签章要求"]["source"]

    unsafe = {
        "客户名称": customer,
        "客户仓库": "另一家器材有限公司",
        "签章要求": "物流发展有限器材有限公司（盖椭圆的代码章）",
    }
    repair_contextual_fields(unsafe)
    assert unsafe["签章要求"].startswith("物流发展")


def test_signature_requirement_trims_only_duplicated_samsung_authorization_prefix():
    expected = "三星授权（成都利群通讯有限责任公司）服务中心授权代码：4913766"
    fields = {"签章要求": expected + "三星授权"}
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == expected
    assert corrections["签章要求"]["original"] == expected + "三星授权"

    unrelated = {"签章要求": expected + "业务章"}
    repair_contextual_fields(unrelated)
    assert unrelated["签章要求"] == expected + "业务章"


def test_signature_requirement_trims_one_stamp_overlap_glyph_after_terminal_type():
    customer = "山东鑫舜禹电子科技有限公司"
    fields = {
        "客户名称": customer,
        "客户仓库": customer,
        "签章要求": customer + "维修专用章禹",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == customer + "维修专用章"
    assert corrections["签章要求"]["original"].endswith("专用章禹")

    no_repeated_customer = {
        "客户名称": customer,
        "客户仓库": "另一家公司有限公司",
        "签章要求": customer + "维修专用章禹",
    }
    repair_contextual_fields(no_repeated_customer)
    assert no_repeated_customer["签章要求"].endswith("专用章禹")


def test_reviewed_ship_to_repairs_repeated_dezhou_customer_and_enables_code_fill():
    expected = "德州市德城区利星电子产品销售店（个体工商户）"
    fields = {
        "客户名称": expected,
        "客户仓库": "德州市德城区利星电产品销售店（个体商户）",
        "ShipToCode": "0005970275",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["客户名称"] == expected
    assert fields["客户仓库"] == expected
    assert fields["SoldToCode"] == "0005970275"
    assert "ShipToCode 主数据" in corrections["客户仓库"]["source"]


def test_reviewed_ship_to_repairs_jilin_authorization_requirement():
    customer = "吉林省欧昇科技有限公司"
    fields = {
        "客户名称": customer,
        "客户仓库": customer,
        "ShipToCode": "0003197601",
        "SoldToCode": "0003197601",
        "签章要求": "三星电子权服务中心0431-88693789收数量",
    }

    corrections = repair_contextual_fields(fields)

    assert fields["签章要求"] == "三星电子授权服务中心0431-88693789"
    assert corrections["签章要求"]["source"].startswith("ShipToCode 签章主数据")


def test_weak_warehouse_and_two_sided_requirement_noise_use_same_page_evidence():
    customer = "山东鑫舜禹电子科技有限公司"
    fields = {
        "客户名称": customer,
        "客户仓库": "L",
        "SoldToCode": "0004775444",
        "ShipToCode": "0004775444",
        "签章要求": f"L{customer}东鑫舞用",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["客户仓库"] == customer
    assert corrections["客户仓库"]["original"] == "L"
    assert fields["签章要求"] == customer
    assert corrections["签章要求"]["original"] == f"L{customer}东鑫舞用"


def test_receipt_address_repairs_two_dropouts_from_near_exact_customer_address():
    fields = {
        "客户地址": "广东省广州市番禺区市桥街道桥东路28号 广州 190 CN 511400",
        "收货地址": "州市番禺区市桥街道桥东路28号 州 190 CN 511400",
    }
    corrections = repair_contextual_fields(fields)

    assert fields["收货地址"] == "广州市番禺区市桥街道桥东路28号广州190CN511400"
    assert corrections["收货地址"]["original"].startswith("州市")

    different = {
        "客户地址": "广东省广州市番禺区市桥街道桥东路28号广州190CN511400",
        "收货地址": "广州市番禺区另一条路99号广州190CN511400",
    }
    repair_contextual_fields(different)
    assert "另一条路99号" in different["收货地址"]


def test_pickup_requirement_recovers_missing_samsung_prefix_from_station_and_stamp_type():
    fields = {"签章要求": "务中心取机专用章5785258站"}
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "三星电子服务中心取机专用章5785258站"
    assert "站编号" in corrections["签章要求"]["source"]


def test_pickup_station_number_without_stamp_type_is_not_expanded():
    fields = {"签章要求": "物流服务中心5785258站"}
    repair_contextual_fields(fields)
    assert fields["签章要求"] == "物流服务中心5785258站"


def test_known_customer_requirement_uses_audited_stamp_master_data():
    fields = {
        "客户名称": "深圳市星睿奇光电有限公司",
        "客户仓库": "深圳市星睿奇光电有限公司深圳仓库",
        "签章要求": "助储埋海有镇有限公司仓储部收货章",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "深圳市星睿奇光电有限公司仓储部收货章"
    assert "主数据" in corrections["签章要求"]["source"]

    unconfirmed = {
        "客户名称": "深圳市星睿奇光电有限公司",
        "客户仓库": "完全不同仓库有限公司",
        "签章要求": "助储埋海有镇有限公司仓储部收货章",
    }
    repair_contextual_fields(unconfirmed)
    assert unconfirmed["签章要求"] == "助储埋海有镇有限公司仓储部收货章"


def test_jingjiang_requirement_uses_repeated_customer_and_reviewed_stamp_master():
    fields = {
        "客户名称": "靖江市中联通讯设备经营部",
        "客户仓库": "靖江市中联通讯设备经营部",
        "签章要求": "靖江率建胶要研售发整等收",
    }
    corrections = repair_contextual_fields(fields)
    assert fields["签章要求"] == "靖江市中联通讯售后专用章"
    assert "主数据" in corrections["签章要求"]["source"]


def test_bilingual_samsung_service_stamp_needs_brand_service_and_stamp_type():
    requirement = "三星电子客户服务中心服务专用章"
    matched = compare_seal_text(
        requirement,
        ["收货客户 SAMSUNG 服务 SAMSUNG 用章"],
    )
    assert matched["status"] == "匹配"
    assert matched["reliable"] is True
    assert matched["score"] == 0.9

    brand_only = compare_seal_text(requirement, ["SAMSUNG 客户签名"])
    assert brand_only["reliable"] is False


def test_bilingual_samsung_customer_center_needs_brand_and_full_center_type():
    requirement = "三星SAMSUNG客户服务中心"
    matched = compare_seal_text(
        requirement,
        ["SAMSUNG 服务中心 地址：良凤山东路25号"],
    )
    assert matched["status"] == "匹配"
    assert matched["reliable"] is True
    assert matched["score"] == 0.9

    assert compare_seal_text(requirement, ["SAMSUNG 客户签名"])["reliable"] is False
    assert compare_seal_text(requirement, ["普通服务中心"])["reliable"] is False


def test_fragmented_local_samsung_round_stamp_needs_brand_place_and_center():
    requirement = "三星电子孝感服务中心"
    matched = compare_seal_text(
        requirement,
        ["感品电星考感中心服务服务中中中用美星电中"],
    )

    assert matched["status"] == "匹配"
    assert matched["reliable"] is True
    assert matched["score"] == 0.9
    assert compare_seal_text(
        requirement, ["星电武汉服务中心"]
    )["reliable"] is False
    assert compare_seal_text(
        requirement, ["考感普通服务中心"]
    )["reliable"] is False


def test_product_table_groups_rows_and_columns_with_validation():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("产品类别", 1, .13, .40, .07, .01),
        TextObservation("物料编号", 1, .29, .40, .07, .01),
        TextObservation("数量", 1, .60, .40, .04, .01),
        TextObservation("EAN码", 1, .86, .40, .05, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("Gl", .3, .16, .42, .02, .01),
        TextObservation("SW-F9560ZSCCHC星夜银", .5, .24, .419, .17, .012),
        TextObservation("512G", 1, .39, .421, .04, .01),
        TextObservation("WO02", .5, .50, .42, .04, .01),
        TextObservation("1", .3, .61, .42, .01, .01),
        TextObservation("0.672", .5, .69, .42, .05, .01),
        TextObservation("0.001", .5, .76, .42, .04, .01),
        TextObservation("8806095665559", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]
    table = parse_product_table(rows)
    assert len(table["rows"]) == 1
    values = table["rows"][0]["values"]
    assert values["产品类别"] == "G1"
    assert values["物料编号"].startswith("SM-")
    assert values["出库仓库"] == "W002"
    assert values["数量"] == "1"
    assert values["EAN码"] == "8806095665559"
    assert table["rows"][0]["confidences"]["EAN码"] == 1.0


def test_product_table_splits_paddle_merged_weight_and_volume_box():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-X", 1, .25, .42, .08, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.4880.001", .99, .69, .42, .10, .01),
        TextObservation("8806095702766", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]
    detail = parse_product_table(rows)["rows"][0]
    assert detail["values"]["重量"] == "0.488"
    assert detail["values"]["体积"] == "0.001"
    assert detail["sources"]["重量"] == "OCR + 跨列拆分"


def test_product_table_removes_single_non_numeric_prefix_from_weight():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-W9025ZDGCHC陶瓷黑1TB", 1, .25, .42, .18, .01),
        TextObservation("A", 1, .46, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("可。1.286", .80, .69, .42, .06, .01),
        TextObservation("0.002", 1, .76, .42, .04, .01),
        TextObservation("8806095787787", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["values"]["重量"] == "1.286"


def test_single_standard_cover_product_repairs_row_zero_missing_leading_one():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("0", .98, .06, .42, .01, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-R630NZWACHC流沙白", 1, .25, .42, .17, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.220", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095649177", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["original_values"]["行号"] == "0"
    assert detail["values"]["行号"] == "10"
    assert detail["sources"]["行号"] == "标准单行首页首行号结构校正"


def test_row_zero_is_not_rewritten_without_standard_cover_evidence():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("0", 1, .06, .42, .01, .01),
        TextObservation("G2", 1, .16, .42, .02, .01),
        TextObservation("OTHER", 1, .25, .42, .08, .01),
        TextObservation("B", 1, .45, .42, .01, .01),
        TextObservation("X001", 1, .50, .42, .04, .01),
        TextObservation("2", 1, .61, .42, .01, .01),
        TextObservation("1234567890123", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    assert parse_product_table(rows)["rows"][0]["values"]["行号"] == "0"


def test_product_material_rewrites_server_o_as_zero_only_in_samsung_sku():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("GP-TOS926SBERCNFC交互卡片", .98, .25, .42, .18, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.100", 1, .69, .42, .04, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095702766", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["original_values"]["物料编号"] == "GP-TOS926SBERCNFC交互卡片"
    assert detail["values"]["物料编号"] == "GP-T0S926SBERCNFC交互卡片"
    assert detail["sources"]["物料编号"] == "OCR + 代码规则"


def test_unknown_material_family_keeps_letter_o_for_manual_review():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("AB-10MODEL其他产品", .98, .25, .42, .15, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.100", 1, .69, .42, .04, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095702766", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    assert (
        parse_product_table(rows)["rows"][0]["values"]["物料编号"]
        == "AB-10MODEL其他产品"
    )


def test_ean_strips_single_non_numeric_suffix_only_after_checksum_validation():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("F-0F95PCPEGCN纤巧SPen保护壳", .98, .25, .42, .18, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.100", 1, .69, .42, .04, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095635323尼科技", .99, .84, .42, .14, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["original_values"]["EAN码"] == "8806095635323尼科技"
    assert detail["values"]["EAN码"] == "8806095635323"
    assert detail["sources"]["EAN码"] == "OCR + 代码规则"


def test_product_grade_strips_attached_table_punctuation():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-X", 1, .25, .42, .08, .01),
        TextObservation(";A", .6, .44, .42, .03, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.488", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095702766", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]
    assert parse_product_table(rows)["rows"][0]["values"]["等级"] == "A"


def test_long_product_table_uses_strong_column_consensus_for_stamp_occlusion():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("产品类别", 1, .13, .40, .07, .01),
        TextObservation("物料编号", 1, .29, .40, .07, .01),
        TextObservation("等级", 1, .44, .40, .04, .01),
        TextObservation("出库仓库", 1, .50, .40, .07, .01),
    ]
    for index in range(10):
        y = .42 + index * .015
        rows.extend([
            TextObservation(str((index + 1) * 10), 1, .06, y, .03, .01),
            TextObservation("G1", 1, .16, y, .02, .01),
            TextObservation(f"SM-X{index}", 1, .25, y, .08, .01),
            TextObservation("E" if index == 8 else "A", .9, .44, y, .02, .01),
            *([] if index == 9 else [TextObservation("WC80", 1, .50, y, .04, .01)]),
            TextObservation("1", 1, .61, y, .01, .01),
            TextObservation("0.100", 1, .69, y, .05, .01),
            TextObservation("0.001", 1, .76, y, .04, .01),
            TextObservation(f"88060957027{index:02d}", 1, .84, y, .11, .01),
        ])
    rows.append(TextObservation("合计：", 1, .49, .575, .05, .01))

    table = parse_product_table(rows)

    assert len(table["rows"]) == 10
    assert table["rows"][8]["values"]["等级"] == "A"
    assert table["rows"][8]["sources"]["等级"] == "同表列强一致性校正"
    assert table["rows"][9]["values"]["出库仓库"] == "WC80"


def test_product_table_repairs_power_bank_description_touching_grade():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("B-P4520XUELCN20000mAh移动电A", .99, .23, .42, .20, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.534", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806097027850", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]
    detail = parse_product_table(rows)["rows"][0]
    assert detail["values"]["物料编号"] == "B-P4520XUELCN20000mAh移动电源"
    assert detail["values"]["等级"] == "A"


def test_product_table_uses_verified_ean_catalog_for_one_glyph_dropout():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-S9280ZKHCHC钛512G", .99, .23, .42, .19, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.399", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095307947", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]
    detail = parse_product_table(rows)["rows"][0]
    assert detail["values"]["物料编号"] == "SM-S9280ZKHCHC钛黑512G"
    assert detail["sources"]["物料编号"] == "EAN 校验商品目录校正"


def test_product_table_uses_verified_ean_for_missing_ceramic_color_word():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-W9025ZDGCHC陶瓷1TB", .99, .23, .42, .21, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("1.286", 1, .69, .42, .05, .01),
        TextObservation("0.002", 1, .76, .42, .04, .01),
        TextObservation("8806095787787", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["values"]["物料编号"] == "SM-W9025ZDGCHC陶瓷黑1TB"
    assert detail["original_values"]["物料编号"] == "SM-W9025ZDGCHC陶瓷1TB"
    assert detail["sources"]["物料编号"] == "EAN 校验商品目录校正"


def test_product_table_uses_verified_ean_for_missing_xuanyao_color_word():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-W9026AKDCHC玄曜512G", .99, .23, .42, .21, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("1.326", 1, .69, .42, .05, .01),
        TextObservation("0.002", 1, .76, .42, .04, .01),
        TextObservation("8806097727347", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["values"]["物料编号"] == "SM-W9026AKDCHC玄曜黑512G"
    assert detail["original_values"]["物料编号"] == "SM-W9026AKDCHC玄曜512G"
    assert detail["sources"]["物料编号"] == "EAN 校验商品目录校正"


def test_product_table_backfills_missing_ean_only_for_exact_verified_material():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-F9660ZKGCHC秘影黑512G", .99, .23, .42, .20, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.726", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["original_values"]["EAN码"] == ""
    assert detail["values"]["EAN码"] == "8806097433736"
    assert detail["confidences"]["EAN码"] == 0.99
    assert "已复核 EAN" in detail["sources"]["EAN码"]


def test_single_product_uses_total_weight_when_stamp_hides_detail_numbers():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-X", 1, .25, .42, .08, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("0.000", 1, .76, .42, .04, .01),
        TextObservation("8806095860480", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
        TextObservation("0.", .87, .686, .44, .037, .01),
        TextObservation("409", .99, .718, .44, .033, .01),
        TextObservation("0.000", 1, .76, .44, .04, .01),
    ]
    detail = parse_product_table(rows)["rows"][0]
    assert detail["values"]["数量"] == "1"
    assert detail["values"]["重量"] == "0.409"
    assert detail["sources"]["重量"] == "合计行交叉验证"


def test_single_product_replaces_nonnumeric_quantity_from_total_row():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-X", 1, .25, .42, .08, .01),
        TextObservation("A", 1, .45, .42, .01, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("pae", .51, .61, .42, .03, .01),
        TextObservation("0.403", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806097476061", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
        TextObservation("1", 1, .61, .44, .01, .01),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["original_values"]["数量"] == "pae"
    assert detail["values"]["数量"] == "1"
    assert detail["sources"]["数量"] == "合计行交叉验证"


def test_product_table_deskews_row_and_splits_merged_material_grade():
    rows = [
        TextObservation("行号", 1, .046, .404, .039, .017),
        TextObservation("产品类别", 1, .129, .403, .070, .016),
        TextObservation("物料编号", 1, .291, .402, .068, .014),
        TextObservation("等级出库仓库", 1, .435, .399, .119, .015),
        TextObservation("数量", 1, .601, .397, .039, .017),
        TextObservation("重量", 1, .698, .396, .039, .014),
        TextObservation("体积", 1, .765, .395, .041, .016),
        TextObservation("EAN码", 1, .868, .395, .048, .014),
        TextObservation("8806095665559", 1, .835, .411, .115, .013),
        TextObservation("0.672", 1, .693, .412, .051, .013),
        TextObservation("0.001", 1, .742, .412, .065, .011),
        TextObservation("W002", 1, .499, .415, .042, .012),
        TextObservation("1", 1, .614, .415, .015, .012),
        TextObservation("SM-F9560ZSGCHC星夜银512GA", 1, .231, .416, .238, .014),
        TextObservation("G1", 1, .151, .419, .026, .014),
        TextObservation("10", 1, .054, .420, .024, .014),
        TextObservation("合计：", 1, .492, .430, .050, .017),
    ]

    detail = parse_product_table(rows)["rows"][0]

    assert detail["values"] == {
        "行号": "10", "产品类别": "G1",
        "物料编号": "SM-F9560ZSGCHC星夜银512G", "等级": "A",
        "出库仓库": "W002", "数量": "1", "重量": "0.672",
        "体积": "0.001", "EAN码": "8806095665559",
    }
    assert detail["sources"]["等级"] == "OCR + 跨列拆分"


def test_product_table_does_not_guess_grade_after_chinese_material_description():
    rows = [
        TextObservation("行号", 1, .04, .40, .04, .01),
        TextObservation("10", 1, .05, .42, .02, .01),
        TextObservation("G1", 1, .15, .42, .02, .01),
        TextObservation("F-RS938CBEGCN护盾减震型保护A", .99, .22, .42, .25, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .62, .42, .01, .01),
        TextObservation("0.105", 1, .70, .42, .04, .01),
        TextObservation("0.000", 1, .78, .42, .04, .01),
        TextObservation("8806097031062", 1, .85, .42, .12, .01),
        TextObservation("合计：", 1, .50, .45, .04, .01),
    ]

    values = parse_product_table(rows)["rows"][0]["values"]

    # The final A is not moved into the grade column.  This known EAN does,
    # however, provide independent product-catalog evidence for the material.
    assert values["物料编号"] == "F-RS938CBEGCN护盾减震型保护壳"
    assert values["等级"] == ""
