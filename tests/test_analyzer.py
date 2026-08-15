from datetime import date

import pytest

from receipt_ocr.analyzer import (
    ReceiptAnalyzer,
    _reject_date_before_creation,
    _recover_missing_product_grades,
    _recover_signature_requirement,
    _is_neighboring_label_misread_as_receipt_note,
    _recover_confirmed_template_note,
    _trim_signature_requirement_candidate,
    _fuse_product_descriptions,
    _fuse_product_material,
    _exclude_printed_footer_rows_from_seal_context,
    _find_signature_requirement_row,
    _find_low_confidence_date_audit,
    _find_confirmed_far_lower_date,
    _apply_single_paddle_safety,
    _apply_code_stamp_business_id,
    _company_conflict_allows_clipped_prefix_server_recheck,
    _conflicting_receipt_dates,
    _server_mobile_dominant_date_from_artifacts,
    _repeated_server_required_date_from_artifacts,
    _cross_model_far_lower_strict_date,
    _needs_low_confidence_date_audit,
    _day_slot_confirms_value,
    _max_channel_truncated_mismatch_candidate,
    _missing_year_separator_consensus_from_artifacts,
    _parse_missing_year_separator_full_date,
    _cross_year_nondestructive_consensus_from_artifacts,
    _cross_model_max_channel_required_with_truncated_conflict,
    _cross_model_slot_required_date,
    _cross_model_server_strict_component_date,
    _parse_full_year_month_day_audit,
    _parse_partial_year_month_day_audit,
    _partial_year_day_before_audit_from_artifacts,
    _white_day_conflict_prefilter_from_artifacts,
    _white_day_conflict_audit_candidate,
    _date_component_consensus_from_artifacts,
    _server_cross_geometry_strict_date_from_artifacts,
    _server_strict_component_consensus_from_artifacts,
    _required_month_slot_conflict_consensus_from_artifacts,
    _parse_date_slot_digit,
    _has_shared_specific_stamp_type,
    _strong_unread_colored_stamp_route,
    _reconstruct_business_acceptance_from_audit,
    _reconstruct_exact_company_stamp_from_region,
    _reconstruct_overlapping_repair_stamp,
    _reconstruct_business_acceptance_from_mobile_bands,
    _shared_long_organization_suffix,
    _prefer_detail_field,
    _prefer_detail_requirement,
    _parse_server_audit_candidate,
    _unique_server_mobile_otsu_candidate,
    _trailing_numeric_month_day,
    _ocr_model_config,
    combine_region_texts,
    decide_overall,
)
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.parser import (
    compare_seal_text,
    estimate_date_confidence,
    find_receipt_date,
    normalize_text,
    parse_date,
    parse_product_table,
)


def test_server_model_config_is_exposed_in_result_payload(monkeypatch):
    monkeypatch.setenv("PADDLE_SERVER_MAX_SIDE", "1800")

    assert _ocr_model_config({"page": "paddle"}) == {}
    assert _ocr_model_config({"page": "paddle_server"}) == {
        "page_model": "PP-OCRv5 Server",
        "server_max_page_side": 1800,
        "server_page_scaling": "最长边超过上限时等比缩放推理，归一化坐标映射不变",
    }


def _dominant_date_artifacts(
    candidate: str = "2025年5月10日",
    conflict: str = "20年3月10日",
) -> list[dict]:
    artifacts = []
    for variant in ("紧凑区域", "宽区域"):
        artifacts.append({
            "variant": variant,
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "secondary_ocr_variants": [
                {"preprocessing": "原始裁剪", "ocr_texts": ["20年5月10日"]},
                {"preprocessing": "去印章色", "ocr_texts": ["0年5月10日"]},
            ],
            "date_line_ocr_variants": [
                {
                    "preprocessing": "日期行 Server 大模型复核不一致日期",
                    "ocr_texts": [candidate],
                },
            ],
        })
    if conflict:
        artifacts[1]["date_line_ocr_variants"].append({
            "preprocessing": "日期行去表格线 Mobile 人工候选",
            "ocr_texts": [conflict],
        })
    return artifacts


def test_server_mobile_dominant_date_accepts_near_date_with_one_isolated_noise():
    assert _server_mobile_dominant_date_from_artifacts(
        _dominant_date_artifacts(), "2025-05-11"
    ) == date(2025, 5, 10)


def test_server_mobile_dominant_date_rejects_truncated_day_far_from_requirement():
    assert _server_mobile_dominant_date_from_artifacts(
        _dominant_date_artifacts(
            candidate="2025年7月2日", conflict=""
        ),
        "2025-07-25",
    ) is None


def test_server_mobile_dominant_date_rejects_repeated_or_strict_conflict():
    artifacts = _dominant_date_artifacts()
    artifacts[0]["date_line_ocr_variants"].append({
        "preprocessing": "日期行原图 Mobile",
        "ocr_texts": ["2025年5月9日"],
    })
    assert _server_mobile_dominant_date_from_artifacts(
        artifacts, "2025-05-11"
    ) is None


def _repeated_server_artifacts(
    *, candidate: str = "2025年8月7日", extra: str = ""
) -> list[dict]:
    texts = [candidate]
    artifact = {
        "variant": "宽区域",
        "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_line_ocr_variants": [
            {
                "preprocessing": "日期行 Server 大模型低置信度候选",
                "ocr_texts": texts,
            },
            {
                "preprocessing": "日期行最大通道去彩色三倍放大 Server 跨几何复核",
                "ocr_texts": [candidate],
            },
            {
                "preprocessing": "日期行灰度自动对比三倍放大 Server 人工候选",
                "ocr_texts": [candidate],
            },
        ],
    }
    if extra:
        artifact["date_line_ocr_variants"].append({
            "preprocessing": "日期行原图 Mobile",
            "ocr_texts": [extra],
        })
    return [artifact]


def test_repeated_server_required_date_accepts_three_strict_transforms():
    assert _repeated_server_required_date_from_artifacts(
        _repeated_server_artifacts(), "2025-08-07"
    ) == date(2025, 8, 7)


def test_repeated_server_required_date_rejects_two_views_or_other_date():
    two_views = _repeated_server_artifacts()
    two_views[0]["date_line_ocr_variants"].pop()
    assert _repeated_server_required_date_from_artifacts(
        two_views, "2025-08-07"
    ) is None
    assert _repeated_server_required_date_from_artifacts(
        _repeated_server_artifacts(extra="2025年8月27日"), "2025-08-07"
    ) is None


def test_repeated_server_required_date_rejects_complete_wrong_candidate():
    assert _repeated_server_required_date_from_artifacts(
        _repeated_server_artifacts(candidate="2025年8月2日"),
        "2025-08-07",
    ) is None


def test_receipt_date_before_creation_is_rejected_but_preserved_for_review():
    from datetime import date

    actual, rejected, creation = _reject_date_before_creation(
        date(2025, 2, 1), "2025-03-18"
    )
    assert actual is None
    assert rejected == date(2025, 2, 1)
    assert creation == date(2025, 3, 18)


def test_reprint_date_does_not_reject_receipt_after_embedded_waybill_date():
    from datetime import date

    actual, rejected, lower_bound = _reject_date_before_creation(
        date(2025, 10, 2), "2025-10-09", "W20250929-005411"
    )

    assert actual == date(2025, 10, 2)
    assert rejected is None
    assert lower_bound == date(2025, 9, 29)


def test_repeated_strict_far_date_is_exposed_only_as_low_confidence_audit():
    rows = [
        TextObservation("2025.8.5", .35, .75, .78, .12, .02),
        TextObservation("2025.8.5", .35, .75, .78, .12, .02),
    ]

    actual, evidence = _find_low_confidence_date_audit(rows)

    assert actual.isoformat() == "2025-08-05"
    assert evidence.confidence == .35
    assert _find_low_confidence_date_audit(rows[:1]) == (None, None)


def test_neighboring_quantity_label_is_not_accepted_as_receipt_note():
    assert _is_neighboring_label_misread_as_receipt_note("实收数量：")
    assert _is_neighboring_label_misread_as_receipt_note("拒收数量（台）")
    assert not _is_neighboring_label_misread_as_receipt_note(
        "如未签实收数量视为整单完整签收"
    )


def test_fixed_note_recovers_when_quantity_label_was_selected_but_note_label_exists():
    rows = [TextObservation("签收说明：", .99, .04, .64, .08, .02)]
    assert _recover_confirmed_template_note(rows, "实收数量：") == (
        "如未签实收数量视为整单完整签收"
    )
    assert _recover_confirmed_template_note(rows, "物流发") == (
        "如未签实收数量视为整单完整签收"
    )
    assert _recover_confirmed_template_note([], "实收数量：") == ""


def test_fixed_note_recovers_from_receipt_footer_when_note_label_is_missed():
    rows = [TextObservation("签章要求：测试章", .99, .04, .62, .25, .02)]

    assert _recover_confirmed_template_note(rows, "") == (
        "如未签实收数量视为整单完整签收"
    )


def test_oval_code_stamp_can_match_two_form_codes_without_company_guessing():
    initial = compare_seal_text(
        "广州市知星通讯器材有限公司（盖椭圆的代码章）",
        ["代码：6092851"],
    )
    checked = _apply_code_stamp_business_id(
        initial,
        "广州市知星通讯器材有限公司（盖椭圆的代码章）",
        ["代码：6092851"],
        {"SoldToCode": "0006092851", "ShipToCode": "0006092851"},
    )

    assert checked["status"] == "匹配"
    assert checked["reliable"] is True
    assert checked["recognized_code"] == "6092851"

    unsafe = _apply_code_stamp_business_id(
        compare_seal_text(
            "广州市知星通讯器材有限公司（盖椭圆的代码章）",
            ["代码：6092851"],
        ),
        "广州市知星通讯器材有限公司（盖椭圆的代码章）",
        ["代码：6092851"],
        {"SoldToCode": "0006092851", "ShipToCode": "0006092852"},
    )
    assert unsafe["reliable"] is False


def test_printed_requirement_line_is_excluded_from_seal_context():
    rows = [
        TextObservation("签章要求：", .99, .04, .62, .08, .012),
        TextObservation("北京罗凡尼科技发展有限公司", .99, .12, .62, .25, .012),
        TextObservation("签收说明：", .99, .04, .645, .08, .012),
        TextObservation("如未签实收数量视为整单完整签收", .99, .12, .645, .30, .012),
        TextObservation("北京罗凡尼科技发展有限公司收货章", .80, .72, .75, .22, .04),
    ]

    filtered = _exclude_printed_footer_rows_from_seal_context(rows)

    assert [row.text for row in filtered] == ["北京罗凡尼科技发展有限公司收货章"]


def test_noisy_signature_label_still_marks_a_real_footer():
    noisy = TextObservation("[b章要求", .91, .04, .47, .08, .014)
    assert _find_signature_requirement_row([noisy]) is noisy
    assert _find_signature_requirement_row([
        TextObservation("章要求", .91, .04, .20, .08, .014)
    ]) is None


def test_obscured_signature_label_uses_multiple_receipt_footer_anchors():
    rows = [
        TextObservation("签收说明：如未签实收数量视为整单完整签收", .96, .03, .48, .42, .015),
        TextObservation("实收数量：（台）", .95, .58, .48, .15, .015),
        TextObservation("拒收数量：（台）", .95, .78, .48, .15, .015),
        TextObservation("收货客户", .92, .72, .51, .12, .015),
        TextObservation("仓库接收人", .91, .58, .54, .12, .015),
    ]

    inferred = _find_signature_requirement_row(rows)

    assert inferred is not None
    assert inferred.text == "推断签收页脚"
    assert inferred.y < rows[0].y


def test_sparse_generic_footer_words_do_not_invent_receipt_footer():
    rows = [
        TextObservation("日期", .95, .04, .48, .05, .015),
        TextObservation("盖章", .95, .72, .52, .05, .015),
        TextObservation("拒收数量", .95, .72, .20, .10, .015),
    ]

    assert _find_signature_requirement_row(rows) is None


def test_original_color_crop_ocr_is_audit_only_for_seal_matching(tmp_path, monkeypatch):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (200, 100), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if "-original" in path.name and backend == "paddle":
            return [TextObservation("北京罗凡尼科技发展有限公司", .99, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.1, .6, .3, .2, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
    )

    assert texts == []
    assert artifacts[0]["secondary_original_text"] == "北京罗凡尼科技发展有限公司"


def test_original_color_crop_below_footer_can_support_seal_matching(tmp_path, monkeypatch):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (200, 100), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if "-original" in path.name and backend == "paddle":
            return [TextObservation("哈尔滨晨光智能科技有限公司维修专用章", .99, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.1, .60, .3, .2, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement="哈尔滨晨光智能科技有限公司维修专用章",
        footer_anchor_y=.47,
    )

    assert "哈尔滨晨光智能科技有限公司维修专用章" in texts
    assert artifacts[0]["secondary_original_used_for_matching"] is True


def test_unreliable_color_isolated_seal_uses_server_model_audit(tmp_path, monkeypatch):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (200, 100), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle_server" and "-unwrapped" in path.name:
            return [
                TextObservation("福州中昊通信技术有", .95, 0, 0, 1, .4),
                TextObservation("限公司", .95, 0, .5, 1, .4),
            ]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement="福州中昊通信技术有限公司",
    )

    assert "福州中昊通信技术有限公司" in texts
    assert artifacts[0]["server_audit_text"] == "福州中昊通信技术有 | 限公司"


def test_shallow_company_round_seal_uses_server_unwrapped_bands(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        size = (2160, 792) if destination.name.endswith("-unwrapped.png") else (240, 240)
        Image.new("RGB", size, "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            return [TextObservation("湖南和联电子科", .93, 0, 0, 1, 1)]
        if backend == "paddle_server":
            if path.name == "seal-0-unwrapped-band-1.png":
                return [TextObservation("湖南和联电子科技", .95, 0, 0, 1, 1)]
            if path.name == "seal-0-unwrapped-band-2.png":
                return [TextObservation("公司", .92, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "湖南和联电子科技有限公司"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source,
        [],
        [SealRegion(.6, .6, .25, .08, "red", "收货客户章", .2)],
        tmp_path / "artifacts",
        "/artifacts",
        "vision",
        "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert len(artifacts[0]["unwrapped_band_urls"]) == 3
    band_variants = [
        row for row in artifacts[0]["server_audit_variants"]
        if row["preprocessing"].startswith("圆章展开分带")
    ]
    assert [row["ocr_texts"] for row in band_variants] == [
        ["湖南和联电子科技"], ["公司"], []
    ]


@pytest.mark.parametrize(
    ("requirement", "regular_text"),
    [
        ("太原市伊加壹电子服务总汇", "太原市伊加壹电"),
        (
            "银川恒久致远通信器材有限责任公司客户服务中心",
            "川组久致远通信器材有限负行",
        ),
    ],
)
def test_service_organization_seal_routes_to_safe_server_audit(
    tmp_path, monkeypatch, requirement, regular_text
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (220, 220), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "vision" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(regular_text, .75, 0, 0, 1, 1)]
        if backend == "paddle_server" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(requirement, .96, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert artifacts[0]["server_audit_used_for_matching"] is True
    assert artifacts[0]["server_audit_text"] == requirement


def test_low_similarity_weak_color_service_organization_does_not_route_to_server(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (220, 220), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")
    server_calls = []

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "vision" and path.name == "seal-0-unwrapped.png":
            return [TextObservation("收货客户章", .80, 0, 0, 1, 1)]
        if backend == "paddle_server":
            server_calls.append(path.name)
            return [TextObservation("太原市伊加壹电子服务总汇", .96, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    _texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .05)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement="太原市伊加壹电子服务总汇",
    )

    assert server_calls == []
    assert "server_audit_backend" not in artifacts[0]


def test_zero_score_strong_color_service_organization_routes_to_server(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (220, 220), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")
    server_calls = []

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle_server":
            server_calls.append(path.name)
            return [
                TextObservation(
                    "太原市伊加壹电子服务总汇", .96, 0, 0, 1, 1
                )
            ]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source,
        [],
        [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts",
        "/artifacts",
        "vision",
        "paddle",
        requirement="太原市伊加壹电子服务总汇",
    )

    check = compare_seal_text("太原市伊加壹电子服务总汇", texts)
    assert server_calls
    assert check["reliable"] is True
    assert artifacts[0]["server_audit_used_for_matching"] is True


def test_round_stamp_rotated_unwrap_recovers_opposite_facing_suffix(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "深圳市天音科技发展有限公司", .96, 0, 0, 1, 1
            )]
        if backend == "paddle_server":
            if path.name == "seal-0-unwrapped.png":
                return [TextObservation(
                    "深圳市天音科技发展有限公司", .97, 0, 0, 1, 1
                )]
            if path.name == "seal-0-unwrapped-rotated-180.png":
                return [TextObservation("业务专用章", .89, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "深圳市天音科技发展有限公司业务专用章"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert artifacts[0]["unwrapped_rotated_url"].endswith(
        "seal-0-unwrapped-rotated-180.png"
    )
    rotated = next(
        item for item in artifacts[0]["server_audit_variants"]
        if item["preprocessing"] == "圆章展开 180°"
    )
    assert rotated["ocr_texts"] == ["业务专用章"]


def test_round_stamp_rotated_color_sheet_recovers_center_stamp_type(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "杭州松峰电子科技有限公司", .96, 0, 0, 1, 1
            )]
        if (
            backend == "paddle_server"
            and path.name == "seal-0-color-isolated-rotations.png"
        ):
            return [TextObservation("业务专用章", .92, 0, 0, 1, 1)]
        if backend == "paddle_server" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "杭州松峰电子科技有限公司", .97, 0, 0, 1, 1
            )]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "杭州松峰电子科技有限公司业务专用章"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert artifacts[0]["color_isolated_rotations_url"].endswith(
        "seal-0-color-isolated-rotations.png"
    )
    rotated_sheet = next(
        item for item in artifacts[0]["server_audit_variants"]
        if item["preprocessing"] == "保留章色旋转对照图"
    )
    assert rotated_sheet["ocr_texts"] == ["业务专用章"]


def test_branch_round_stamp_rotations_reconstruct_only_exact_observed_parts(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr(
        "receipt_ocr.analyzer.extract_region_text",
        lambda *_a: (
            "0.490.0011备注仓库接收人盖章北昌后服士"
            "供应商：中国外运物流发展有限公司广州分公司"
        ),
    )

    organization = "北京亨通达科技有限公司西城西单分公司"
    stamp_type = "北售后服务专用章"

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            # This reaches only the narrow 0.50 routing score and must not be
            # mistaken for the complete branch organization by itself.
            return [TextObservation("京亨通达", .91, 0, 0, 1, 1)]
        if backend == "paddle_server":
            if path.name == "seal-0-unwrapped.png":
                return [TextObservation(organization, .97, 0, 0, 1, 1)]
            if path.name == "seal-0-color-isolated-rotations.png":
                return [TextObservation(stamp_type, .94, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = organization + stamp_type
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    variants = artifacts[0]["server_audit_variants"]
    assert next(
        row for row in variants
        if row["preprocessing"] == "保留章色旋转对照图"
    )["ocr_texts"] == [stamp_type]
    assert next(
        row for row in variants
        if row["preprocessing"] == "同章区完整公司与章型重组（无字符补写）"
    )["ocr_texts"] == [requirement]


def test_round_stamp_rotated_color_sheet_requires_strong_company_evidence(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")
    audited_paths = []

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "大连北华通信设备有限公司", .96, 0, 0, 1, 1
            )]
        if backend == "paddle_server":
            audited_paths.append(path.name)
            if path.name == "seal-0-color-isolated-rotations.png":
                return [TextObservation(
                    "深圳市天音科技发展有限公司业务专用章",
                    .99, 0, 0, 1, 1,
                )]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "深圳市天音科技发展有限公司业务专用章"
    texts, _ = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["reliable"] is False
    assert "seal-0-color-isolated-rotations.png" not in audited_paths


def test_company_and_stamp_type_from_different_regions_do_not_self_combine():
    requirement = "深圳市天音科技发展有限公司业务专用章"

    check = compare_seal_text(
        requirement,
        ["深圳市天音科技发展有限公司", "业务专用章"],
    )

    assert check["status"] == "无法判断"
    assert check["reliable"] is False


def test_complete_near_name_company_vetoes_fuzzy_seal_pass():
    requirement = "大连北华通信设备有限公司售后专用章"

    check = compare_seal_text(
        requirement,
        [
            "售后专用章",
            "大连允华通信设备有限公司",
            "大连允华通信设备有限公司售后专用章",
        ],
    )

    assert check["status"] == "无法判断"
    assert check["reliable"] is False
    assert check["company_conflict"] is True
    assert "不同的公司全称" in check["message"]


def test_expected_company_core_corroborates_one_glyph_ocr_variant():
    requirement = "杭州松峰电子科技有限公司业务专用章"

    check = compare_seal_text(
        requirement,
        [
            "杭州松蜂电子科技有限公司",
            "杭州松峰电子科技有",
            "杭州松峰电子科技有业务专用章",
        ],
    )

    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert check["company_conflict"] is False


def test_expected_company_core_can_corroborate_alternate_ocr_spelling():
    requirement = "大连北华通信设备有限公司售后专用章"

    check = compare_seal_text(
        requirement,
        [
            "大连允华通信设备有限公司",
            "大连北华通信设备有限公司",
            "售后专用章",
            "大连北华通信设备有限公司售后专用章",
        ],
    )

    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert check["company_conflict"] is False


def test_server_cannot_override_regular_complete_company_conflict(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "大连允华通信设备有限公司", .96, 0, 0, 1, 1
            )]
        if backend == "paddle" and path.name == "seal-0-color-isolated.png":
            return [TextObservation("售后业务专用章", .92, 0, 0, 1, 1)]
        if backend == "paddle_server" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "大连北华通信设备有限公司", .99, 0, 0, 1, 1
            )]
        if backend == "paddle_server":
            return [TextObservation("售后业务专用章", .95, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "大连北华通信设备有限公司售后业务专用章"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "无法判断"
    assert check["reliable"] is False
    assert check["company_conflict"] is True
    assert artifacts[0]["server_audit_used_for_matching"] is False
    assert "仅供人工复核" in artifacts[0]["server_audit_rejection_reason"]
    assert "大连北华通信设备有限公司" in artifacts[0]["server_audit_text"]
    assert "大连北华通信设备有限公司" not in texts
    assert not any(
        row["preprocessing"] == "同章区完整公司与章型重组（无字符补写）"
        for row in artifacts[0]["server_audit_variants"]
    )


def test_clipped_expected_prefix_allows_server_to_resolve_local_ocr_conflict(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and path.name == "seal-0-unwrapped.png":
            return [
                TextObservation("济南新字航科技发展有限公司", .96, 0, 0, 1, 1),
                TextObservation("南新宇航科技发展有限公司", .94, 0, 0, 1, 1),
            ]
        if backend == "paddle_server" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "济南新宇航科技发展有限公司", .99, 0, 0, 1, 1
            )]
        if backend == "paddle_server":
            return [TextObservation("业务专用章", .97, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "济南新宇航科技发展有限公司业务专用章"
    assert _company_conflict_allows_clipped_prefix_server_recheck(
        requirement,
        ["济南新字航科技发展有限公司", "南新宇航科技发展有限公司"],
    ) is True
    assert _company_conflict_allows_clipped_prefix_server_recheck(
        "大连北华通信设备有限公司售后专用章",
        ["大连允华通信设备有限公司", "大连华通信设备有限公司"],
    ) is False

    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert artifacts[0]["server_audit_used_for_matching"] is True
    assert "恰好缺失首字" in artifacts[0]["server_audit_conflict_override_reason"]


def test_specific_stamp_type_can_route_low_company_evidence_to_server_audit():
    assert _has_shared_specific_stamp_type(
        "张家港市一达信息服务有限公司维修专用章",
        ["仓库接收人", "维修专用章"],
    ) is True
    assert _has_shared_specific_stamp_type(
        "杭州索兰科技有限公司维修专章",
        ["维修专用章"],
    ) is True
    assert _has_shared_specific_stamp_type(
        "青岛和沃电子技术有限公司业务专用章",
        ["业务专用"],
    ) is True
    assert _has_shared_specific_stamp_type(
        "三星电子孝感服务中心",
        ["服务中"],
    ) is True
    assert _has_shared_specific_stamp_type(
        "洛阳东利通信有限公司业务受理（2）",
        ["业务受理（2）"],
    ) is True
    assert _has_shared_specific_stamp_type(
        "三星电子客户服务中心服务专用章",
        ["SAMSUNG", "服务"],
    ) is True
    # A generic suffix is common to unrelated stamps and must not be enough
    # to incur the large-model route or influence the final matcher.
    assert _has_shared_specific_stamp_type(
        "浙江大唐电子通信有限公司维修专用章",
        ["专用章"],
    ) is False
    assert _has_shared_specific_stamp_type(
        "北京罗凡尼科技发展有限公司",
        ["维修专用章"],
    ) is False
    assert _has_shared_specific_stamp_type(
        "三星电子客户服务中心服务专用章",
        ["SAMSUNG"],
    ) is False


def test_strong_unread_colored_stamp_route_requires_visual_and_org_evidence():
    assert _strong_unread_colored_stamp_route(
        "三星电子青岛维修中心", 0.0, 0.12
    ) is True
    assert _strong_unread_colored_stamp_route(
        "牡丹江市万邦通讯器材商店", 0.0, 0.08
    ) is True
    assert _strong_unread_colored_stamp_route(
        "三星电子青岛维修中心", 0.01, 0.12
    ) is False
    assert _strong_unread_colored_stamp_route(
        "三星电子青岛维修中心", 0.0, 0.059
    ) is False
    assert _strong_unread_colored_stamp_route(
        "普通收货章", 0.0, 0.20
    ) is False
    assert _strong_unread_colored_stamp_route(
        "三星电子孝感服务中心", 0.096, 0.1363
    ) is True
    assert _strong_unread_colored_stamp_route(
        "三星电子孝感服务中心", 0.462, 0.1647
    ) is True
    assert _strong_unread_colored_stamp_route(
        "三星电子孝感服务中心", 0.501, 0.1647
    ) is False
    assert _strong_unread_colored_stamp_route(
        "三星电子孝感服务中心", 0.096, 0.079
    ) is False


@pytest.mark.parametrize(
    ("server_text", "expected_reliable", "expected_conflict"),
    [
        ("洛阳东利通信有限公司业务受理（2）", True, False),
        ("洛阳西利通信有限公司业务受理（2）", False, True),
    ],
)
def test_business_acceptance_server_audit_keeps_company_conflict_guard(
    tmp_path, monkeypatch, server_text, expected_reliable, expected_conflict
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 180), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "vision" and path.name == "seal-0-unwrapped.png":
            return [TextObservation("业务受理（2）", .92, 0, 0, 1, 1)]
        if backend == "paddle_server" and path.name == "seal-0-unwrapped.png":
            return [TextObservation(server_text, .96, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "洛阳东利通信有限公司业务受理（2）"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["reliable"] is expected_reliable
    assert check["company_conflict"] is expected_conflict
    assert artifacts[0]["server_audit_used_for_matching"] is True
    assert artifacts[0]["server_audit_text"] == server_text


def test_business_acceptance_reconstructs_exact_same_region_company_fragments(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (240, 180), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "vision" and path.name == "seal-0-unwrapped.png":
            return [TextObservation("业务受理专用章", .92, 0, 0, 1, 1)]
        if backend == "paddle_server" and path.name == "seal-0-unwrapped.png":
            return [
                TextObservation("业务受理专用章", .97, 0, 0, 1, .2),
                TextObservation("利达电子技术有限公司", .96, 0, .3, 1, .2),
                TextObservation("郑州广", .95, 0, .6, 1, .2),
            ]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "郑州广利达电子技术有限公司业务受理"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    reconstructed = next(
        item for item in artifacts[0]["server_audit_variants"]
        if item["preprocessing"] == "同章区公司片段重组（无字符补写）"
    )
    assert reconstructed["ocr_texts"] == [requirement]


def test_business_acceptance_fragment_reconstruction_needs_exact_parts():
    requirement = "郑州广利达电子技术有限公司业务受理"
    assert _reconstruct_business_acceptance_from_audit(
        requirement,
        ["业务受理专用章", "利达电子技术有限公司", "郑州广"],
    ) == requirement
    assert _reconstruct_business_acceptance_from_audit(
        requirement,
        ["业务受理专用章", "西利达电子技术有限公司", "郑州广"],
    ) == ""


@pytest.mark.parametrize(
    ("requirement", "audit_texts"),
    [
        (
            "云南邮维科技有限公司检测专用章（3）",
            ["云南邮维科技有限公司", "检测专用章", "(3)"],
        ),
        (
            "北京华康君泰贸易有限公司售后业务专用章",
            ["北京华康君泰贸易有限公司", "售后业务专用章"],
        ),
        (
            "云南邮维科技有限公司检测专用章（3）",
            ["云南邮维科技有限公司89660899-18", "检测专用章", "（3）"],
        ),
    ],
)
def test_exact_company_and_specific_stamp_rows_reconstruct_without_new_glyphs(
    requirement, audit_texts
):
    assert _reconstruct_exact_company_stamp_from_region(
        requirement, audit_texts
    ) == normalize_text(requirement)


def test_exact_branch_company_and_prefixed_type_reconstruct_without_new_glyphs():
    requirement = (
        "北京亨通达科技有限公司西城西单分公司北售后服务专用章"
    )
    organization = "北京亨通达科技有限公司西城西单分公司"
    stamp_type = "北售后服务专用章"
    assert _reconstruct_exact_company_stamp_from_region(
        requirement, [organization, stamp_type]
    ) == requirement
    assert _reconstruct_exact_company_stamp_from_region(
        requirement, ["北京亨通达科技有限公司", stamp_type]
    ) == ""
    assert _reconstruct_exact_company_stamp_from_region(
        requirement, [organization, "售后服务专用章"]
    ) == ""


@pytest.mark.parametrize(
    ("requirement", "audit_texts"),
    [
        (
            "云南邮维科技有限公司检测专用章（3）",
            ["云南邮维科技有限公司", "检测专用章"],
        ),
        (
            "北京华康君泰贸易有限公司售后业务专用章",
            ["北京华康君泰贸易有限公司", "售后业务专用"],
        ),
        (
            "北京亨通达科技有限公司西城西单分公司北售后服务专用章",
            ["北京亨通达科技有限公司", "售后服务专用章"],
        ),
        (
            "北京华康君泰贸易有限公司售后业务专用章",
            ["北京华康君泰贸易有限公", "售后业务专用章"],
        ),
        (
            "北京华康君泰贸易有限公司售后业务专用章",
            ["北京华康君泰贸易有限公司仓储", "售后业务专用章"],
        ),
    ],
)
def test_exact_company_stamp_reconstruction_rejects_missing_or_extra_parts(
    requirement, audit_texts
):
    assert _reconstruct_exact_company_stamp_from_region(
        requirement, audit_texts
    ) == ""


def test_overlapping_repair_stamp_reconstructs_only_exact_cross_region_parts():
    from receipt_ocr.image_processing import SealRegion

    requirement = "杭州索兰科技有限公司维修专章"
    upper = SealRegion(.70, .46, .23, .16, "red", "收货客户章", .38)
    lower = SealRegion(.73, .54, .23, .16, "red", "收货客户章", .42)
    evidence = [
        {"region": upper, "texts": ["兰科技有限公司"]},
        {"region": lower, "texts": ["杭州索", "维修专用章"]},
    ]

    assert _reconstruct_overlapping_repair_stamp(
        requirement, evidence
    ) == "杭州索兰科技有限公司维修专用章"
    assert _reconstruct_overlapping_repair_stamp(
        "杭州索兰科技有限公司维修专用章", evidence
    ) == "杭州索兰科技有限公司维修专用章"
    assert _reconstruct_overlapping_repair_stamp(
        requirement,
        [evidence[0], {**evidence[1], "texts": ["杭州松", "维修专用章"]}],
    ) == ""
    assert _reconstruct_overlapping_repair_stamp(
        requirement,
        [evidence[0], {**evidence[1], "texts": ["杭州索"]}],
    ) == ""
    non_overlapping = SealRegion(
        .05, .70, .23, .16, "red", "收货客户章", .42
    )
    assert _reconstruct_overlapping_repair_stamp(
        requirement,
        [evidence[0], {"region": non_overlapping, "texts": evidence[1]["texts"]}],
    ) == ""


def test_hybrid_overlapping_repair_stamps_use_two_server_regions(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region, **_kwargs):
        Image.new("RGB", (320, 240), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr(
        "receipt_ocr.analyzer.extract_region_text",
        lambda _rows, region: (
            "兰科技有限公司" if region.y < .5 else "杭州索维修专用章"
        ),
    )

    def fake_ocr(path, *, backend, **_kwargs):
        if backend != "paddle_server":
            return []
        if path.name == "seal-0-unwrapped.png":
            return [TextObservation("兰科技有限公司", .97, 0, 0, 1, 1)]
        if path.name == "seal-1-color-isolated-rotations.png":
            return [
                TextObservation("杭州索", .96, 0, 0, 1, .4),
                TextObservation("维修专用章", .98, 0, .5, 1, .4),
            ]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    regions = [
        SealRegion(.70, .46, .23, .16, "red", "收货客户章", .38),
        SealRegion(.73, .54, .23, .16, "red", "收货客户章", .42),
    ]
    requirement = "杭州索兰科技有限公司维修专章"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], regions,
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["reliable"] is True
    assert check["recognized"] == "杭州索兰科技有限公司维修专用章"
    assert all(
        item["overlapping_region_reconstructed_text"] == check["recognized"]
        for item in artifacts
    )


def test_mobile_rectangular_bands_resolve_only_exact_business_company():
    requirement = "郑州广利达电子技术有限公司业务受理专用章"
    assert _reconstruct_business_acceptance_from_mobile_bands(
        requirement,
        ["郑州广利达电子技术有限公司", "业务文理"],
        ["业务受理"],
    ) == "郑州广利达电子技术有限公司业务受理"
    assert _reconstruct_business_acceptance_from_mobile_bands(
        requirement,
        ["郑州广利达电于技术有限公司", "业务受理"],
        ["业务受理"],
    ) == ""


def test_robust_round_suffix_requires_same_long_observation_from_both_models():
    requirement = "牡丹江市万邦通讯器材商店"
    assert _shared_long_organization_suffix(
        requirement,
        ["市万邦通讯器材商店"],
        ["江市万邦通讯器材商店"],
    ) == "市万邦通讯器材商店"
    assert _shared_long_organization_suffix(
        requirement,
        ["市万邦通讯器材商店"],
        ["江市万帮通讯器材商店"],
    ) == ""
    assert _shared_long_organization_suffix(
        requirement,
        ["器材商店"],
        ["器材商店"],
    ) == ""
    assert _shared_long_organization_suffix(
        "深圳市星睿奇光电有限公司仓储部收货章",
        ["仓储部收货章"],
        ["仓储部收货章"],
    ) == ""


def test_hybrid_robust_round_bounds_use_only_cross_model_observed_suffix(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (600, 600), "white").save(source)

    def fake_save(_source, destination, _region, *, robust_bounds=False):
        size = (2160, 792) if "unwrapped" in destination.name else (600, 300)
        Image.new("RGB", size, "white").save(destination)
        return robust_bounds

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: False
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "vision" and path.name == "seal-0-color-isolated.png":
            return [TextObservation("福市万器通一涵", .60, 0, 0, 1, 1)]
        if "unwrapped-robust-band" in path.name:
            if backend == "paddle":
                return [TextObservation(
                    "市万邦通讯器材商店", .94, 0, 0, 1, 1
                )]
            if backend == "paddle_server":
                return [TextObservation(
                    "江市万邦通讯器材商店", .96, 0, 0, 1, 1
                )]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "牡丹江市万邦通讯器材商店"
    region = SealRegion(.55, .55, .4, .2, "red", "收货客户章", .13)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source,
        [],
        [region],
        tmp_path / "artifacts",
        "/artifacts",
        "vision",
        "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["reliable"] is True
    assert check["recognized"] == "市万邦通讯器材商店"
    assert artifacts[0]["robust_shared_suffix"] == check["recognized"]
    assert len(artifacts[0]["robust_unwrapped_band_urls"]) == 3
    assert "不补写缺失地名" in artifacts[0]["robust_bounds_acceptance_note"]

    single_texts, single_artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source,
        [],
        [region],
        tmp_path / "single-artifacts",
        "/single-artifacts",
        "paddle",
        None,
        requirement=requirement,
    )
    assert compare_seal_text(requirement, single_texts)["reliable"] is False
    assert "robust_unwrapped_url" not in single_artifacts[0]
    assert _reconstruct_business_acceptance_from_mobile_bands(
        requirement,
        ["郑州广利达电子技术有限公司", "洛阳广利达电子技术有限公司"],
        ["业务受理"],
    ) == ""
    assert _reconstruct_business_acceptance_from_mobile_bands(
        requirement,
        ["郑州广利达电子技术有限公司"],
        ["业务文理"],
    ) == ""


def test_hybrid_mobile_rectangular_band_can_resolve_one_company_glyph_conflict(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        size = (1500, 780) if destination.name.endswith("-unwrapped.png") else (600, 300)
        Image.new("RGB", size, "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.seal_region_is_rectangular", lambda *_a: True
    )
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if path.name == "seal-0-color-isolated.png":
            return [TextObservation("业务受理", .96, 0, 0, 1, 1)]
        if path.name == "seal-0-unwrapped.png":
            return [TextObservation(
                "郑州广利达电于技术有限公司", .96, 0, 0, 1, 1
            )]
        if backend == "paddle" and path.name.endswith(
            "rectangular-company-band-2.png"
        ):
            return [TextObservation(
                "郑州广利达电子技术有限公司", .98, 0, 0, 1, 1
            )]
        if backend == "paddle" and path.name.endswith(
            "rectangular-company-band-3.png"
        ):
            return [TextObservation("业务受理", .99, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    requirement = "郑州广利达电子技术有限公司业务受理专用章"
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source,
        [],
        [SealRegion(.55, .55, .4, .15, "red", "收货客户章", .1)],
        tmp_path / "artifacts",
        "/artifacts",
        "vision",
        "paddle",
        requirement=requirement,
    )

    check = compare_seal_text(requirement, texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert check["company_conflict"] is False
    assert check["recognized"] == "郑州广利达电子技术有限公司业务受理"
    assert artifacts[0]["conflict_mobile_band_resolution"] == check["recognized"]
    assert len(artifacts[0]["conflict_mobile_band_urls"]) == 3

    single_model_texts, single_model_artifacts = (
        ReceiptAnalyzer()._recognize_local_seals(
            source,
            [],
            [SealRegion(.55, .55, .4, .15, "red", "收货客户章", .1)],
            tmp_path / "single-model-artifacts",
            "/single-model-artifacts",
            "paddle",
            None,
            requirement=requirement,
        )
    )
    single_model_check = compare_seal_text(requirement, single_model_texts)
    assert single_model_check["reliable"] is False
    assert single_model_check["company_conflict"] is True
    assert "conflict_mobile_band_backend" not in single_model_artifacts[0]


def test_single_paddle_seal_artifact_does_not_require_secondary_backend(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (200, 100), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2)],
        tmp_path / "artifacts", "/artifacts", "paddle",
        requirement="",
    )

    assert texts == []
    assert artifacts[0]["secondary_ocr_backend"] == ""
    assert artifacts[0]["secondary_original_used_for_matching"] is False


def test_low_similarity_numbered_service_stamp_uses_safe_server_audit(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (400, 600), "white").save(source)

    def fake_save(_source, destination, _region):
        Image.new("RGB", (200, 100), "white").save(destination)

    monkeypatch.setattr("receipt_ocr.analyzer.save_region_crop", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_color_isolated_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.save_unwrapped_seal", fake_save)
    monkeypatch.setattr("receipt_ocr.analyzer.extract_region_text", lambda *_a: "")

    def fake_ocr(path, *, backend, **_kwargs):
        if backend == "vision" and "seal-1-isolated" in path.name:
            return [TextObservation("919360章", .60, 0, 0, 1, 1)]
        if (
            backend == "paddle_server"
            and path.name == "seal-0-color-isolated.png"
        ):
            return [
                TextObservation("三星电子维修", .95, 0, 0, 1, .4),
                TextObservation("5972036", .99, 0, .5, 1, .4),
            ]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_ocr)
    texts, artifacts = ReceiptAnalyzer()._recognize_local_seals(
        source, [], [
            SealRegion(.6, .6, .25, .18, "red", "收货客户章", .2),
            SealRegion(.6, .7, .25, .18, "red", "收货客户章", .2),
        ],
        tmp_path / "artifacts", "/artifacts", "vision", "paddle",
        requirement="三星电子维修中心5972036",
    )

    check = compare_seal_text("三星电子维修中心5972036", texts)
    assert check["status"] == "匹配"
    assert check["reliable"] is True
    assert artifacts[0]["server_audit_text"] == "三星电子维修 | 5972036"


def test_windows_single_paddle_route_forces_date_and_seal_review():
    date_check = {"status": "匹配", "confidence": 1.0, "reliable": True}
    seal_check = {"status": "匹配", "confidence": .96, "reliable": True}

    policy = _apply_single_paddle_safety(
        date_check, seal_check,
        {"page": "paddle", "date": "paddle", "seal": "paddle"},
    )

    assert "单一 Paddle" in policy
    assert date_check["confidence"] == .68
    assert seal_check["confidence"] == .68
    assert date_check["reliable"] is False
    assert seal_check["reliable"] is False


def test_cross_model_windows_server_route_keeps_reliable_evidence():
    date_check = {"status": "匹配", "confidence": .92, "reliable": True}
    seal_check = {"status": "匹配", "confidence": .90, "reliable": True}

    policy = _apply_single_paddle_safety(
        date_check, seal_check,
        {"page": "paddle_server", "date": "paddle", "seal": "paddle"},
    )

    assert policy == ""
    assert date_check["reliable"] is True
    assert seal_check["reliable"] is True


def test_windows_hybrid_end_to_end_never_auto_passes_single_model_evidence(
    tmp_path, monkeypatch
):
    from PIL import Image
    from receipt_ocr.image_processing import SealRegion

    source = tmp_path / "windows-receipt.jpg"
    Image.new("RGB", (900, 1400), "white").save(source)
    page_rows = [
        TextObservation("出库单", .99, .42, .05, .16, .03),
        TextObservation("承运商：北京顺丰速运有限公司", .99, .04, .12, .34, .015),
        TextObservation("运单号：W20250101-000001", .99, .04, .15, .30, .015),
        TextObservation("制单日期：2025-01-01", .99, .04, .18, .26, .015),
        TextObservation("客户名称：测试科技有限公司", .99, .04, .22, .30, .015),
        TextObservation("客户仓库：测试科技有限公司", .99, .04, .25, .30, .015),
        TextObservation("客户订单号：7000000001", .99, .04, .28, .26, .015),
        TextObservation("要求到货：2025-01-02", .99, .04, .31, .26, .015),
        TextObservation("收货地址：测试地址", .99, .04, .34, .28, .015),
        TextObservation("签章要求：测试科技有限公司", .99, .04, .47, .30, .015),
    ]
    date_row = TextObservation("2025年1月2日", .99, .80, .60, .16, .02)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: page_rows)
    monkeypatch.setattr("receipt_ocr.analyzer.decode_qr", lambda *_a: "")
    monkeypatch.setattr("receipt_ocr.analyzer.resolve_backend", lambda *_a: "hybrid")
    monkeypatch.setattr(
        "receipt_ocr.analyzer.backend_route",
        lambda *_a: {"page": "paddle", "date": "paddle", "seal": "paddle"},
    )
    monkeypatch.setattr(
        "receipt_ocr.analyzer.backend_route_labels",
        lambda *_a: {"page": "Paddle", "date": "Paddle", "seal": "Paddle"},
    )
    monkeypatch.setattr("receipt_ocr.analyzer.backend_label", lambda *_a: "Paddle")
    monkeypatch.setattr("receipt_ocr.analyzer._recover_signature_requirement", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.detect_seal_regions",
        lambda *_a: [SealRegion(.7, .58, .25, .18, "red", "收货客户章", .2)],
    )
    analyzer = ReceiptAnalyzer()
    monkeypatch.setattr(analyzer, "_recognize_receipt_date", lambda *_a, **_k: ([date_row], []))
    monkeypatch.setattr(
        analyzer, "_recognize_local_seals",
        lambda *_a, **_k: (["测试科技有限公司"], []),
    )
    monkeypatch.setattr(analyzer.seal_api, "recognize", lambda *_a: {"enabled": False})

    result = analyzer.analyze(source, ocr_backend="hybrid")

    assert result["date_check"]["status"] == "匹配"
    assert result["seal_check"]["status"] == "匹配"
    assert result["date_check"]["reliable"] is False
    assert result["seal_check"]["reliable"] is False
    assert result["review_status"] == "待复核"
    assert "单一 Paddle" in result["safety_policy"]


def test_receipt_date_on_or_after_creation_remains_eligible():
    from datetime import date

    actual, rejected, _ = _reject_date_before_creation(
        date(2025, 3, 21), "2025-03-18"
    )
    assert actual == date(2025, 3, 21)
    assert rejected is None


def test_trailing_numeric_month_day_accepts_noisy_leading_year_strokes():
    from datetime import date

    required = date(2025, 4, 7)
    assert _trailing_numeric_month_day("200.4.7", required)
    assert _trailing_numeric_month_day("30.4.7", required)
    assert _trailing_numeric_month_day("-820年4月7日", required)


def test_trailing_numeric_month_day_rejects_other_day_or_explicit_year():
    from datetime import date

    required = date(2025, 4, 7)
    assert not _trailing_numeric_month_day("30.4.8", required)
    assert not _trailing_numeric_month_day("2024.4.7", required)


def test_hybrid_accepts_cross_model_month_day_on_normal_table_line(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1500), "white").save(source)

    def fake_save_crop(_source, destination, _anchor_y, *, tight, raw_destination, color_clean_destination, left=None):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        return (.72, .55, .27, .10)

    def fake_save_line(_source, destination, *, tight, lower=False):
        Image.new("RGB", (350, 80), "white").save(destination)
        return (.20, .30, .75, .60)

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer._save_date_line_crop", fake_save_line)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_args, **_kwargs: [])

    def fake_line(path, *, model_variant):
        name = str(path)
        if "date-tight-line-original" not in name:
            return []
        text = "-820年4月20日" if model_variant == "mobile" else "802年4月20日"
        return [TextObservation(text, .90, .05, .10, .90, .70)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", fake_line)
    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-04-20", None, "", "vision", "paddle"
    )
    actual, _ = find_receipt_date(rows, "2025-04-20")
    assert actual is not None, [row.text for row in rows]
    assert actual.isoformat() == "2025-04-20"


def test_hybrid_rejects_cross_model_month_day_when_region_has_other_date(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1500), "white").save(source)

    def fake_save_crop(
        _source, destination, _anchor_y, *, tight, raw_destination,
        color_clean_destination, left=None,
    ):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        return (.72, .55, .27, .10)

    def fake_save_line(_source, destination, *, tight, lower=False):
        Image.new("RGB", (350, 80), "white").save(destination)
        return (.20, .30, .75, .60)

    def fake_recognize_text(path, *, backend, **_kwargs):
        if backend == "vision" and "date-tight-color-clean" in str(path):
            return [TextObservation("202年4月19日", .88, .1, .1, .8, .3)]
        return []

    def fake_line(path, *, model_variant):
        if "date-tight-line" not in str(path):
            return []
        text = "-820年4月20日" if model_variant == "mobile" else "802年4月20日"
        return [TextObservation(text, .90, .05, .10, .90, .70)]

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer._save_date_line_crop", fake_save_line)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_recognize_text)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", fake_line)

    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-04-20", tmp_path / "artifacts", "/x",
        "vision", "paddle",
    )
    actual, _ = find_receipt_date(rows, "2025-04-20")
    assert actual != date(2025, 4, 20)
    assert _conflicting_receipt_dates(
        [TextObservation("202年4月19日", .88, 0, 0, 1, 1)],
        date(2025, 4, 20),
    ) == {date(2025, 4, 19)}


def _install_hybrid_mismatch_fakes(tmp_path, monkeypatch, *, server_crop: str):
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1500), "white").save(source)

    def fake_save_crop(_source, destination, _anchor_y, *, tight, raw_destination, color_clean_destination, left=None):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        return (.72, .55, .27, .10)

    def fake_save_line(_source, destination, *, tight, lower=False):
        Image.new("RGB", (350, 80), "white").save(destination)
        return (.20, .30, .75, .60)

    def fake_recognize_text(path, *, backend, **_kwargs):
        if backend == "paddle" and "date-wide-original" in str(path):
            return [TextObservation("25年3月6日", .84, .10, .10, .70, .30)]
        return []

    def fake_recognize_line(path, *, model_variant):
        if model_variant == "server" and f"date-{server_crop}-line-original" in str(path):
            return [TextObservation("2025年3月6日", .90, .05, .10, .90, .70)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer._save_date_line_crop", fake_save_line)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_recognize_text)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", fake_recognize_line)
    return source


def test_hybrid_accepts_cross_model_mismatch_from_other_geometry_without_artifacts(tmp_path, monkeypatch):
    source = _install_hybrid_mismatch_fakes(tmp_path, monkeypatch, server_crop="tight")

    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-03-07", None, "", "vision", "paddle"
    )

    actual, _ = find_receipt_date(rows, "2025-03-07")
    assert actual.isoformat() == "2025-03-06"
    assert len(rows) == 2
    assert artifacts == []


def test_hybrid_rejects_mismatch_corroborated_only_in_same_geometry(tmp_path, monkeypatch):
    source = _install_hybrid_mismatch_fakes(tmp_path, monkeypatch, server_crop="wide")

    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-03-07", None, "", "vision", "paddle"
    )

    assert rows == []


def _install_hybrid_line_mismatch_fakes(
    tmp_path, monkeypatch, *, mobile_crop: str, server_crop: str,
):
    from PIL import Image

    source = tmp_path / "receipt-line-mismatch.jpg"
    Image.new("RGB", (1000, 1500), "white").save(source)

    def fake_save_crop(
        _source, destination, _anchor_y, *, tight, raw_destination,
        color_clean_destination, left=None,
    ):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        return (.72, .55, .27, .10)

    def fake_save_line(_source, destination, *, tight, lower=False):
        Image.new("RGB", (350, 80), "white").save(destination)
        return (.20, .30, .75, .60)

    def fake_recognize_line(path, *, model_variant):
        name = str(path)
        if (
            model_variant == "mobile"
            and f"date-{mobile_crop}-line-original" in name
        ):
            return [TextObservation("2025年8月31日", .75, .05, .10, .90, .70)]
        if (
            model_variant == "server"
            and f"date-{server_crop}-line-original" in name
        ):
            return [TextObservation("2025年8月31日", .80, .05, .10, .90, .70)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer._save_date_line_crop", fake_save_line)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", fake_recognize_line)
    return source


def test_mobile_line_and_server_other_geometry_confirm_strict_mismatch(
    tmp_path, monkeypatch,
):
    source = _install_hybrid_line_mismatch_fakes(
        tmp_path, monkeypatch, mobile_crop="tight", server_crop="wide"
    )

    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-09-01", None, "", "vision", "paddle"
    )

    actual, _ = find_receipt_date(rows, "2025-09-01")
    assert actual is not None
    assert actual.isoformat() == "2025-08-31"


def test_mobile_line_mismatch_still_rejects_same_geometry_confirmation(
    tmp_path, monkeypatch,
):
    source = _install_hybrid_line_mismatch_fakes(
        tmp_path, monkeypatch, mobile_crop="tight", server_crop="tight"
    )

    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-09-01", None, "", "vision", "paddle"
    )

    assert rows == []


def _install_hybrid_partial_line_mismatch_fakes(
    tmp_path, monkeypatch, *, server_crop: str, conflicting_mobile: bool = False,
):
    from PIL import Image

    source = tmp_path / "receipt-partial-line-mismatch.jpg"
    Image.new("RGB", (1000, 1500), "white").save(source)

    def fake_save_crop(
        _source, destination, _anchor_y, *, tight, raw_destination,
        color_clean_destination, left=None,
    ):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        return (.72, .55, .27, .10)

    def fake_save_line(_source, destination, *, tight, lower=False):
        Image.new("RGB", (350, 80), "white").save(destination)
        return (.20, .30, .75, .60)

    def fake_recognize_line(path, *, model_variant):
        name = str(path)
        if model_variant == "mobile":
            if "date-wide-line-color-clean" in name:
                return [TextObservation("201年4月29日", .75, .05, .10, .90, .70)]
            if conflicting_mobile and "date-tight-line-color-clean" in name:
                return [TextObservation("205年4月19日", .88, .05, .10, .90, .70)]
        if (
            model_variant == "server"
            and f"date-{server_crop}-line-original" in name
        ):
            return [TextObservation("2025年4月29日", .89, .05, .10, .90, .70)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer._save_date_line_crop", fake_save_line)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", fake_recognize_line)
    return source


def test_partial_mobile_month_day_and_server_other_geometry_confirm_mismatch(
    tmp_path, monkeypatch,
):
    source = _install_hybrid_partial_line_mismatch_fakes(
        tmp_path, monkeypatch, server_crop="tight"
    )

    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-04-30", tmp_path / "artifacts", "/x", "vision", "paddle"
    )

    actual, _ = find_receipt_date(rows, "2025-04-30")
    assert actual is not None and actual.isoformat() == "2025-04-29"
    assert any(
        item.get("acceptance_note")
        == "与 Server 另一几何裁剪完整日期一致"
        for artifact in artifacts for item in artifact["date_line_ocr_variants"]
    )


def test_partial_mobile_month_day_rejects_same_geometry_server(
    tmp_path, monkeypatch,
):
    source = _install_hybrid_partial_line_mismatch_fakes(
        tmp_path, monkeypatch, server_crop="wide"
    )

    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-04-30", None, "", "vision", "paddle"
    )

    assert rows == []


def test_partial_mobile_month_day_rejects_conflicting_mobile_dates(
    tmp_path, monkeypatch,
):
    source = _install_hybrid_partial_line_mismatch_fakes(
        tmp_path, monkeypatch, server_crop="tight", conflicting_mobile=True
    )

    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .50, "2025-04-30", None, "", "vision", "paddle"
    )

    assert rows == []


def test_non_receipt_page_skips_fixed_date_and_seal_pipeline(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "authorization.jpg"
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (800, 1200), "white").save(source)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.recognize_text",
        lambda *_args, **_kwargs: [
            TextObservation("仓库货物接收委托书", .99, .2, .1, .6, .05),
            TextObservation("本公司全权委托", .96, .1, .2, .3, .03),
            TextObservation("仓库联系人", .95, .1, .3, .2, .03),
        ],
    )
    monkeypatch.setattr("receipt_ocr.analyzer.decode_qr", lambda *_args: "")
    monkeypatch.setattr("receipt_ocr.analyzer.resolve_backend", lambda *_args: "paddle")
    monkeypatch.setattr(
        "receipt_ocr.analyzer.backend_route",
        lambda *_args: {"page": "paddle", "date": "paddle", "seal": "paddle"},
    )
    monkeypatch.setattr(
        "receipt_ocr.analyzer.backend_route_labels",
        lambda *_args: {"page": "Paddle", "date": "Paddle", "seal": "Paddle"},
    )
    monkeypatch.setattr("receipt_ocr.analyzer.backend_label", lambda *_args: "Paddle")
    analyzer = ReceiptAnalyzer()
    monkeypatch.setattr(
        analyzer,
        "_recognize_receipt_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("date pipeline ran")),
    )
    monkeypatch.setattr(
        analyzer,
        "_recognize_local_seals",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("seal pipeline ran")),
    )

    result = analyzer.analyze(source, preview)

    assert result["document_type"]["type"] == "warehouse_authorization"
    assert result["review_status"] == "待复核"
    assert result["date_check"]["status"] == "无法判断"
    assert result["seal_check"]["backend"] == "未执行（文档类型分流）"
    assert result["processing_artifacts"] == {"date": [], "seals": []}
    assert preview.exists()


def test_receipt_cover_without_signature_footer_waits_for_continuation(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "7284571207.jpg"
    Image.new("RGB", (800, 1200), "white").save(source)
    rows = [
        TextObservation("出库单", .99, .40, .05, .20, .04),
        TextObservation("承运商：测试物流", .99, .04, .12, .30, .02),
        TextObservation("运单号：W20250422", .99, .04, .15, .30, .02),
        TextObservation("客户名称：北京罗凡尼科技发展有限公司", .99, .04, .20, .50, .02),
        TextObservation("客户订单号：7284571207", .99, .04, .24, .30, .02),
        TextObservation("要求到货：2025-04-25", .99, .04, .28, .30, .02),
        TextObservation("收货地址：北京市", .99, .04, .32, .30, .02),
    ]
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: rows)
    monkeypatch.setattr("receipt_ocr.analyzer.decode_qr", lambda *_a: "")
    monkeypatch.setattr("receipt_ocr.analyzer.resolve_backend", lambda *_a: "paddle")
    monkeypatch.setattr(
        "receipt_ocr.analyzer.backend_route",
        lambda *_a: {"page": "paddle", "date": "paddle", "seal": "paddle"},
    )
    monkeypatch.setattr(
        "receipt_ocr.analyzer.backend_route_labels",
        lambda *_a: {"page": "Paddle", "date": "Paddle", "seal": "Paddle"},
    )
    monkeypatch.setattr("receipt_ocr.analyzer.backend_label", lambda *_a: "Paddle")
    monkeypatch.setattr("receipt_ocr.analyzer._recover_signature_requirement", lambda *_a, **_k: None)
    analyzer = ReceiptAnalyzer()
    monkeypatch.setattr(
        analyzer, "_recognize_receipt_date",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("date pipeline ran")),
    )
    monkeypatch.setattr(
        analyzer, "_recognize_local_seals",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("seal pipeline ran")),
    )

    result = analyzer.analyze(source)

    assert result["document_type"]["type"] == "receipt"
    assert result["date_check"]["status"] == "未识别"
    assert result["seal_check"]["backend"] == "未执行（等待关联续页）"
    assert any("等待商品续页关联" in reason for reason in result["review_reasons"])


def test_dense_cover_product_table_extends_to_last_complete_row():
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("产品类别", 1, .14, .40, .06, .01),
        TextObservation("物料编号", 1, .24, .40, .08, .01),
        TextObservation("出库仓库", 1, .50, .40, .07, .01),
        TextObservation("数量", 1, .61, .40, .04, .01),
        TextObservation("重量", 1, .69, .40, .04, .01),
        TextObservation("体积", 1, .76, .40, .04, .01),
        TextObservation("EAN码", 1, .84, .40, .06, .01),
    ]
    for index in range(12):
        y = .42 + index * .021
        rows.extend([
            TextObservation(str((index + 1) * 10), .99, .05, y, .03, .01),
            TextObservation("G1", .99, .15, y, .03, .01),
            TextObservation(f"GP-T0F{index:03d}ABCGCN交互卡片", .99, .23, y, .19, .01),
            TextObservation("A", .99, .45, y, .02, .01),
            TextObservation("WC80", .99, .51, y, .04, .01),
            TextObservation("1", .99, .61, y, .02, .01),
            TextObservation("0.100", .99, .69, y, .05, .01),
            TextObservation("0.001", .99, .76, y, .04, .01),
            TextObservation(f"880609570{index:04d}", .99, .84, y, .11, .01),
        ])

    table = parse_product_table(rows)

    assert len(table["rows"]) == 12
    assert table["rows"][-1]["values"]["行号"] == "120"


def test_headerless_continuation_table_is_extracted_by_fixed_columns():
    rows = []
    for index, y in enumerate((.04, .06, .08), 1):
        rows.extend([
            TextObservation(str(index * 10), .99, .05, y, .03, .01),
            TextObservation("G1", .99, .15, y, .03, .01),
            TextObservation(f"EF-X{index}00ABCGCN保护壳", .99, .23, y, .19, .01),
            TextObservation("A", .99, .45, y, .02, .01),
            TextObservation("WC80", .99, .51, y, .04, .01),
            TextObservation("1", .99, .61, y, .02, .01),
            TextObservation("0.100", .99, .69, y, .05, .01),
            TextObservation("0.001", .99, .76, y, .04, .01),
            TextObservation(f"88060957027{index:02d}", .99, .84, y, .11, .01),
        ])

    table = parse_product_table(rows)

    assert len(table["rows"]) == 3
    assert table["rows"][1]["values"]["行号"] == "20"
    assert table["rows"][1]["values"]["物料编号"] == "EF-X200ABCGCN保护壳"
    assert table["source"].startswith("无表头续页")


def test_unreliable_non_match_must_wait_for_human_review():
    date = {"status": "匹配", "reliable": True}
    seal = {"status": "不匹配", "reliable": False}
    assert decide_overall(date, seal, ["印章内容无法可靠判断"]) == "需人工复核"


def test_reliable_non_match_can_be_rejected_automatically():
    date = {"status": "匹配", "reliable": True}
    seal = {"status": "不匹配", "reliable": True}
    assert decide_overall(date, seal, []) == "不通过"


def test_reliable_matches_can_pass_automatically():
    date = {"status": "匹配", "reliable": True}
    seal = {"status": "匹配", "reliable": True}
    assert decide_overall(date, seal, []) == "通过"


def test_same_seal_region_fragments_can_form_reliable_evidence():
    fragments = [
        "淤京东维成维修专用章必www.JDCom", "成都", "華修中心",
    ]
    combined = combine_region_texts(fragments)
    check = compare_seal_text("成都京东维修中心维修专用章", fragments + [combined])
    assert check["status"] == "匹配"
    assert check["reliable"] is True


def test_fuller_similar_signature_requirement_can_replace_primary_ocr():
    assert _prefer_detail_requirement(
        "三星电维修中2310637", "三星电子维修中心2310637"
    ) is True
    assert _prefer_detail_requirement("贵州宏羿科技有限公司", "无关公司的其他印章") is False
    assert _prefer_detail_requirement(
        "杭州索兰科技有限公司维修专章",
        "杭州索兰科技有限公司维修专用章",
    ) is False


def test_long_detail_can_replace_unusable_signature_fragment_but_stays_reviewable():
    assert _prefer_detail_requirement("た", "深圳市星睿奇光电有限公司仓储部收货章") is True


def test_fuller_similar_customer_name_can_replace_primary_ocr():
    assert _prefer_detail_field(
        "州昊君电科技有限公司", "广州昊君电子科技有限公司"
    ) is True
    assert _prefer_detail_field("贵州宏羿科技有限公司", "无关客户有限公司") is False


def test_same_ean_product_fusion_keeps_code_and_adds_vision_description():
    assert _fuse_product_material(
        "SM-S9180ZKHCHC悠远512G", "SM-S918OZKIICIIC悠远黑 512G"
    ) == "SM-S9180ZKHCHC悠远黑512G"
    assert _fuse_product_material(
        "SM-S9180ZKHCHC悠远512G", "SM-S918OZKIICIIC悠远黑 256G"
    ) == ""

    primary = {
        "rows": [{
            "values": {"行号": "10", "产品类别": "G1", "物料编号": "SM-S9180ZKHCHC悠远512G", "等级": "A", "出库仓库": "W002", "数量": "1", "重量": "0.400", "体积": "0.000", "EAN码": "8806094705492"},
            "original_values": {"物料编号": "SM-S9180ZKHCHC悠远512G"},
            "confidences": {"行号": 1, "产品类别": 1, "物料编号": .99, "等级": 1, "出库仓库": 1, "数量": 1, "重量": 1, "体积": 1, "EAN码": 1},
            "sources": {}, "low_confidence_columns": [],
        }],
        "confidence": .99, "source": "表格",
    }
    secondary = {
        "rows": [{
            "values": {"物料编号": "SM-S918OZKIICIIC悠远黑 512G", "EAN码": "8806094705492"},
            "confidences": {"物料编号": .7},
        }]
    }

    _fuse_product_descriptions(primary, secondary)

    row = primary["rows"][0]
    assert row["values"]["物料编号"] == "SM-S9180ZKHCHC悠远黑512G"
    assert row["original_values"]["物料编号_Vision"] == "SM-S918OZKIICIIC悠远黑 512G"
    assert row["sources"]["物料编号"] == "Paddle编码 + Vision商品描述补全"
    assert "物料编号" in row["low_confidence_columns"]


def test_missing_product_grade_can_be_recovered_from_enlarged_crop(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.png"
    Image.new("RGB", (1000, 1600), "white").save(source)
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("SM-X", 1, .25, .42, .08, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.488", 1, .69, .42, .05, .01),
        TextObservation("0.001", 1, .76, .42, .04, .01),
        TextObservation("8806095702766", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
        TextObservation("签章要求：测试公司", 1, .04, .47, .20, .01),
    ]
    table = parse_product_table(rows)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.recognize_text",
        lambda *_args, **_kwargs: [TextObservation("A", 1, .39, .47, .05, .15)],
    )
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *_args, **_kwargs: [],
    )

    recovered = _recover_missing_product_grades(source, rows, table, "vision")

    assert recovered["rows"][0]["values"]["等级"] == "A"
    assert "局部放大" in recovered["rows"][0]["sources"]["等级"]


def test_missing_grade_uses_exact_row_cell_recognition(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.png"
    Image.new("RGB", (1000, 1600), "white").save(source)
    rows = [
        TextObservation("行号", 1, .05, .40, .04, .01),
        TextObservation("10", 1, .06, .42, .02, .01),
        TextObservation("G1", 1, .16, .42, .02, .01),
        TextObservation("F-RS938CBEGCN护盾减震型保护A", .99, .22, .42, .25, .01),
        TextObservation("W002", 1, .50, .42, .04, .01),
        TextObservation("1", 1, .61, .42, .01, .01),
        TextObservation("0.105", 1, .69, .42, .05, .01),
        TextObservation("0.000", 1, .76, .42, .04, .01),
        TextObservation("8806097031062", 1, .84, .42, .11, .01),
        TextObservation("合计：", 1, .49, .44, .05, .01),
        TextObservation("签章要求：测试公司", 1, .04, .47, .20, .01),
    ]
    table = parse_product_table(rows)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *_args, **_kwargs: [TextObservation("A", .95, 0, 0, 1, 1)],
    )

    recovered = _recover_missing_product_grades(source, rows, table, "vision")

    assert recovered["rows"][0]["values"]["等级"] == "A"


def test_missing_signature_requirement_can_be_recovered_from_fixed_line(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.png"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *_args, **_kwargs: [
            TextObservation(
                "签章要求：深圳市星睿奇光电有限公司仓储部收货章",
                .96, 0, 0, 1, 1,
            )
        ],
    )

    recovered = _recover_signature_requirement(source, [], "", "paddle")

    assert recovered["value"] == "深圳市星睿奇光电有限公司仓储部收货章"
    assert recovered["confidence"] == .96


def test_signature_retry_cannot_worsen_customer_entity_reading(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.png"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *_args, **_kwargs: [
            TextObservation("签章要求：太原前物嘉服务总汇整签收", .96, 0, 0, 1, 1)
        ],
    )

    recovered = _recover_signature_requirement(
        source, [], "太原市物嘉子服务总汇", "paddle",
        customer="太原市伊加壹电子服务总汇",
    )

    assert recovered is None


def test_signature_retry_cannot_replace_complete_numbered_service_center(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.png"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *_args, **_kwargs: [
            TextObservation(
                "签章要求：三星电子维修心2310637筑业业兴",
                .91, 0, 0, 1, 1,
            )
        ],
    )

    recovered = _recover_signature_requirement(
        source, [], "三星电子维修中心2310637", "paddle",
        customer="北京东润丽达科技有限公司",
    )

    assert recovered is None


def test_signature_retry_cannot_worsen_one_glyph_short_samsung_center(tmp_path, monkeypatch):
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *_a, **_k: [
            TextObservation(
                "签章要求：三电子孝感服务中单整收",
                .91, 0, 0, 1, 1,
            )
        ],
    )

    recovered = _recover_signature_requirement(
        source,
        [],
        "三星电子孝感服务中",
        "paddle",
        customer="湖北楚飞网络科技有限公司",
    )

    assert recovered is None


def test_signature_line_trims_neighboring_quantity_noise_at_business_boundary():
    assert _trim_signature_requirement_candidate(
        "深圳市星睿奇光电有限公司仓储部收货章实收数量"
    ) == "深圳市星睿奇光电有限公司仓储部收货章"
    assert _trim_signature_requirement_candidate(
        "三星电子服务中心取机专用章（2）6237143站电话：02081061101数量"
    ) == "三星电子服务中心取机专用章（2）6237143站电话：02081061101"
    assert _trim_signature_requirement_candidate(
        "三星电子维修中心2310637数签收"
    ) == "三星电子维修中心2310637"
    assert _trim_signature_requirement_candidate(
        "云南邮维科技有限公司检测专用章（3）实收数量"
    ) == "云南邮维科技有限公司检测专用章（3）"
    assert _trim_signature_requirement_candidate(
        "哈尔滨晨光智能科技有限公司维修专用章045153608531数签收"
    ) == "哈尔滨晨光智能科技有限公司维修专用章045153608531"
    assert _trim_signature_requirement_candidate(
        "三星电子授权服务中心0431-88693789收数量"
    ) == "三星电子授权服务中心0431-88693789"
    assert _trim_signature_requirement_candidate(
        "三星授权（成都市欣金维电子）服务中心授权代码：4910964实收数"
    ) == "三星授权（成都市欣金维电子）服务中心授权代码：4910964"
    assert _trim_signature_requirement_candidate(
        "三星授权（成都市欣金维电子）服务中心授权代码：4910964三星授权（成：市欣全："
    ) == "三星授权（成都市欣金维电子）服务中心授权代码：4910964"
    assert _trim_signature_requirement_candidate(
        "湖南承远三星电子服务中心收"
    ) == "湖南承远三星电子服务中心"
    assert _trim_signature_requirement_candidate(
        "高峰13604157361为整"
    ) == "高峰13604157361"
    assert _trim_signature_requirement_candidate(
        "高峰13604157361为整单完"
    ) == "高峰13604157361"


def test_signature_line_preserves_station_phone_and_company_structure():
    assert _trim_signature_requirement_candidate(
        "站代码：5988247 02081769324州中启通信科技有限公司定收数量"
    ) == "站代码：5988247 02081769324州中启通信科技有限公司"
    assert _trim_signature_requirement_candidate(
        "三星电子服务中心上海信威站代码6237106收数量"
    ) == "三星电子服务中心上海信威站代码6237106"


def test_tight_and_wide_date_line_consensus_can_corroborate_required_date(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image
    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, **_kwargs):
        name = str(path)
        if "tight-line" in name:
            return [TextObservation("2025年2月23日", .92, 0, 0, 1, 1)]
        if "wide-line-color-clean" in name:
            return [TextObservation("20252月23日", .88, 0, 0, 1, 1)]
        return [TextObservation("1105年2月23", .7, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-02-23", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-02-23")
    assert actual == date(2025, 2, 23)
    assert any(
        item.get("acceptance_note") == "紧裁与宽裁日期行一致，作为相互印证"
        for artifact in artifacts for item in artifact["date_line_ocr_variants"]
    )


def test_one_crop_date_line_agreement_never_promotes_required_date(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    from receipt_ocr.analyzer import ReceiptAnalyzer
    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, **_kwargs):
        if "tight-line" in str(path):
            return [TextObservation("2025年2月9日", .96, 0, 0, 1, 1)]
        return [TextObservation("2025年2月日", .8, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-02-09", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-02-09")
    assert actual == date(2025, 2, 9)
    assert estimate_date_confidence(rows, "2025-02-09", actual) < .72
    assert all(
        not item.get("accepted_texts")
        for artifact in artifacts
        for item in artifact["date_line_ocr_variants"]
        if "三倍放大" not in item.get("preprocessing", "")
    )
    assert any(
        item.get("acceptance_note") == "单一大模型放大图证据，仅供人工复核"
        for artifact in artifacts
        for item in artifact["date_line_ocr_variants"]
    )


def test_server_cross_year_consensus_is_audit_only(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    from receipt_ocr.analyzer import (
        ReceiptAnalyzer,
        _find_low_confidence_date_audit,
    )
    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(_path, *, model_variant="mobile", **_kwargs):
        if model_variant == "server":
            return [TextObservation("2024年4月26日", .97, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-04-26", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    assert find_receipt_date(rows, "2025-04-26")[0] is None
    audit_date, audit_row = _find_low_confidence_date_audit(rows)
    assert audit_date == date(2024, 4, 26)
    assert audit_row is not None and audit_row.confidence == .35
    accepted = [
        text
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
        for text in variant.get("accepted_texts", [])
    ]
    assert "2024年4月26日" in accepted


def test_cross_year_consensus_requires_two_paddle_models_in_both_geometries():
    from datetime import date

    from receipt_ocr.analyzer import (
        _cross_year_strict_consensus_from_artifacts,
    )

    def artifact(variant):
        return {
            "variant": variant,
            "ocr_backend": "vision",
            "ocr_variants": [],
            "secondary_ocr_backend": "paddle",
            "secondary_ocr_variants": [{
                "preprocessing": "原始裁剪",
                "ocr_texts": ["2024年4月26日"],
            }],
            "date_line_ocr_backend": "paddle",
            "date_line_ocr_variants": [{
                "preprocessing": "日期行 Server 大模型复核",
                "ocr_texts": ["2024年4月26日"],
            }],
        }

    artifacts = [artifact("紧凑区域"), artifact("宽区域")]
    evidence = _cross_year_strict_consensus_from_artifacts(
        artifacts, "2025-04-26"
    )
    assert evidence is not None
    assert evidence["date"] == date(2024, 4, 26)
    assert len(evidence["support"]) == 4

    missing_server = [artifact("紧凑区域"), artifact("宽区域")]
    missing_server[1]["date_line_ocr_variants"] = []
    assert _cross_year_strict_consensus_from_artifacts(
        missing_server, "2025-04-26"
    ) is None

    strict_conflict = [artifact("紧凑区域"), artifact("宽区域")]
    strict_conflict[1]["secondary_ocr_variants"][0]["ocr_texts"].append(
        "2024年4月25日"
    )
    assert _cross_year_strict_consensus_from_artifacts(
        strict_conflict, "2025-04-26"
    ) is None


def _component_consensus_artifact() -> dict:
    return {
        "variant": "紧凑区域",
        "ocr_backend": "macOS Vision",
        "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_slot_ocr_variants": [
            {
                "slot": "完整年份槽位",
                "model": model,
                "preprocessing": "最大通道去彩色",
                "ocr_texts": ["2025年"],
            }
            for model in ("mobile", "server")
        ] + [
            {
                "slot": "月份数字窄槽",
                "model": model,
                "preprocessing": "最大通道去彩色",
                "ocr_texts": ["1"],
            }
            for model in ("mobile", "server")
        ],
        "date_line_ocr_variants": [
            {
                "preprocessing": "日期行去印章色",
                "ocr_texts": ["206年月5日"],
            },
            {
                "preprocessing": "日期行最大通道去彩色三倍放大 Server 跨几何复核",
                "ocr_texts": ["202年月5日"],
            },
        ],
        "ocr_variants": [],
        "secondary_ocr_variants": [],
    }


def test_date_component_consensus_is_fully_ocr_owned_and_reliable():
    evidence = _date_component_consensus_from_artifacts(
        [_component_consensus_artifact()]
    )

    assert evidence is not None
    assert evidence["date"] == date(2025, 1, 5)
    assert evidence["support"]["models"] == ["mobile", "server"]
    assert _parse_date_slot_digit("01", maximum=12) == 1
    assert _parse_date_slot_digit("1月", maximum=12) is None


def test_date_component_consensus_never_promotes_pure_paddle_windows_route():
    artifact = _component_consensus_artifact()
    artifact["ocr_backend"] = "PaddleOCR PP-OCRv5 Mobile"
    artifact["secondary_ocr_backend"] = ""

    assert _date_component_consensus_from_artifacts([artifact]) is None


def _server_strict_component_artifacts() -> list[dict]:
    strict_label = (
        "日期行最大通道去彩色三倍放大 Server 跨几何复核"
    )
    slots = [
        {
            "slot": "Server双几何完整年份上下文槽位",
            "model": model,
            "preprocessing": "最大通道去彩色",
            "ocr_texts": [
                "2025年3" if model == "mobile" else "2025年9"
            ],
        }
        for model in ("mobile", "server")
    ] + [
        {
            "slot": "Server双几何月日上下文槽位",
            "model": model,
            "preprocessing": "最大通道去彩色",
            "ocr_texts": ["9月1" if model == "mobile" else "9月1日"],
        }
        for model in ("mobile", "server")
    ]
    return [
        {
            "variant": geometry,
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "ocr_variants": [],
            "secondary_ocr_variants": [],
            "date_line_ocr_variants": [
                {
                    "preprocessing": strict_label,
                    "ocr_texts": ["2025年9月1日"],
                },
                {
                    "preprocessing": "日期行原图",
                    "ocr_texts": ["20年3月1日", "20年8月1日"],
                },
            ],
            "date_slot_ocr_variants": slots if geometry == "紧凑区域" else [],
        }
        for geometry in ("紧凑区域", "宽区域")
    ]


def test_server_strict_component_consensus_is_ocr_owned_and_cross_geometry():
    artifacts = _server_strict_component_artifacts()
    candidate = _server_cross_geometry_strict_date_from_artifacts(artifacts)
    evidence = _server_strict_component_consensus_from_artifacts(artifacts)

    assert candidate == date(2025, 9, 1)
    assert evidence is not None
    assert evidence["date"] == candidate
    assert evidence["support"]["server_geometries"] == [
        "紧凑区域", "宽区域"
    ]


def test_server_strict_component_consensus_ignores_saved_audit_candidate():
    artifacts = _server_strict_component_artifacts()
    artifacts[0]["date_line_ocr_variants"].append({
        "preprocessing": "日期行 Server 大模型低置信度候选",
        "ocr_texts": ["2025年3月1日"],
    })

    evidence = _server_strict_component_consensus_from_artifacts(artifacts)

    assert evidence is not None
    assert evidence["date"] == date(2025, 9, 1)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_wide",
        "strict_conflict",
        "mobile_year_conflict",
        "mobile_month_day_conflict",
        "partial_day_conflict",
        "windows_single_backend",
    ),
)
def test_server_strict_component_consensus_rejects_incomplete_evidence(
    mutation,
):
    artifacts = _server_strict_component_artifacts()
    tight = artifacts[0]
    if mutation == "missing_wide":
        artifacts.pop()
    elif mutation == "strict_conflict":
        artifacts[1]["date_line_ocr_variants"].append({
            "preprocessing": "日期行原图",
            "ocr_texts": ["2025年8月1日"],
        })
    elif mutation == "mobile_year_conflict":
        tight["date_slot_ocr_variants"][0]["ocr_texts"] = ["2024年3"]
    elif mutation == "mobile_month_day_conflict":
        tight["date_slot_ocr_variants"][2]["ocr_texts"] = ["3月1日"]
    elif mutation == "partial_day_conflict":
        tight["date_line_ocr_variants"][1]["ocr_texts"].append(
            "20年8月2日"
        )
    else:
        tight["ocr_backend"] = "PaddleOCR PP-OCRv5 Mobile"
        tight["secondary_ocr_backend"] = ""

    assert _server_strict_component_consensus_from_artifacts(
        artifacts
    ) is None


def test_server_strict_component_helper_requires_each_model_component():
    candidate = date(2025, 9, 1)
    variants = _server_strict_component_artifacts()[0][
        "date_slot_ocr_variants"
    ]
    texts = ["2025年9月1日", "20年3月1日", "20年8月1日"]
    assert _cross_model_server_strict_component_date(
        candidate, variants, texts
    ) == candidate

    for index in range(len(variants)):
        reduced = [dict(item) for item in variants]
        reduced[index] = {**reduced[index], "ocr_texts": []}
        assert _cross_model_server_strict_component_date(
            candidate, reduced, texts
        ) is None


def _month_slot_conflict_artifacts():
    month_slots = [
        {
            "slot": "月份数字窄槽",
            "model": model,
            "preprocessing": preprocessing,
            "ocr_texts": ["7"],
        }
        for model in ("mobile", "server")
        for preprocessing in (
            "最大通道去彩色", "最大通道去彩色并去横线"
        )
    ]
    return [
        {
            "variant": "紧凑区域",
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "ocr_variants": [],
            "secondary_ocr_variants": [],
            "date_line_ocr_variants": [{
                "preprocessing": "日期行 Server 大模型复核不一致日期",
                "ocr_texts": ["2025年7月1日"],
            }],
            "date_slot_ocr_variants": month_slots,
        },
        {
            "variant": "宽区域",
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "ocr_variants": [],
            "secondary_ocr_variants": [],
            "date_line_ocr_variants": [
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 Mobile 跨几何复核"
                    ),
                    "ocr_texts": ["2025年7月1日"],
                },
                {
                    "preprocessing": "日期行去表格线三倍放大 Server 人工候选",
                    "ocr_texts": ["2025年1月1日"],
                },
            ],
        },
    ]


def test_required_month_slot_resolves_single_server_line_clean_conflict():
    evidence = _required_month_slot_conflict_consensus_from_artifacts(
        _month_slot_conflict_artifacts(), "2025-07-01"
    )

    assert evidence is not None
    assert evidence["date"] == date(2025, 7, 1)
    assert evidence["conflict"] == date(2025, 1, 1)
    assert evidence["month"] == 7


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_month_cell",
        "wrong_month_cell",
        "windows_single_backend",
        "same_geometry_support",
        "mobile_strict_conflict",
        "non_line_clean_conflict",
        "third_date",
    ],
)
def test_required_month_slot_keeps_conflicts_review_only(mutation):
    artifacts = _month_slot_conflict_artifacts()
    slots = artifacts[0]["date_slot_ocr_variants"]
    if mutation == "missing_month_cell":
        slots.pop()
    elif mutation == "wrong_month_cell":
        slots[-1]["ocr_texts"] = ["1"]
    elif mutation == "windows_single_backend":
        artifacts[0]["ocr_backend"] = "PaddleOCR PP-OCRv5 Mobile"
        artifacts[0]["secondary_ocr_backend"] = ""
    elif mutation == "same_geometry_support":
        artifacts[1]["date_line_ocr_variants"][0]["ocr_texts"] = []
        artifacts[0]["date_line_ocr_variants"].append({
            "preprocessing": "日期行 Mobile 完整日期",
            "ocr_texts": ["2025年7月1日"],
        })
    elif mutation == "mobile_strict_conflict":
        artifacts[1]["date_line_ocr_variants"][1]["preprocessing"] = (
            "日期行 Mobile 去表格线"
        )
    elif mutation == "non_line_clean_conflict":
        artifacts[1]["date_line_ocr_variants"][1]["preprocessing"] = (
            "日期行 Server 大模型原图"
        )
    else:
        artifacts[0]["date_line_ocr_variants"].append({
            "preprocessing": "日期行 Server 大模型其他候选",
            "ocr_texts": ["2025年7月2日"],
        })

    assert _required_month_slot_conflict_consensus_from_artifacts(
        artifacts, "2025-07-01"
    ) is None


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("server_month", "2"),
        ("server_day", "202年月6日"),
        ("strict_conflict", "2025年1月6日"),
    ],
)
def test_date_component_consensus_rejects_disagreement_or_full_date_conflict(
    mutation, value,
):
    artifact = _component_consensus_artifact()
    if mutation == "server_month":
        artifact["date_slot_ocr_variants"][-1]["ocr_texts"] = [value]
    elif mutation == "server_day":
        artifact["date_line_ocr_variants"][-1]["ocr_texts"] = [value]
    else:
        artifact["date_line_ocr_variants"].append({
            "preprocessing": "日期行 Server 大模型低置信度候选",
            "ocr_texts": [value],
        })

    assert _date_component_consensus_from_artifacts([artifact]) is None


def test_upscaled_table_clean_server_candidate_is_visible_but_unreliable(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        if (
            model_variant == "server"
            and "wide-line-table-clean-upscaled" in str(path)
        ):
            return [TextObservation("20年3月2日", .92, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-03-02", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-03-02")
    assert actual == date(2025, 3, 2)
    assert estimate_date_confidence(rows, "2025-03-02", actual) < .72
    assert all(
        artifact.get("date_line_table_clean_upscaled_url")
        for artifact in artifacts
    )
    assert any(
        item.get("acceptance_note") == "单一大模型放大图证据，仅供人工复核"
        for artifact in artifacts
        for item in artifact["date_line_ocr_variants"]
    )


def test_server_audit_candidate_never_replaces_explicit_month_from_requirement():
    from datetime import date

    required = date(2025, 8, 9)
    assert _parse_server_audit_candidate("1月5日", required) is None
    assert _parse_server_audit_candidate("0月9日", required) is None
    assert _parse_server_audit_candidate("2240月5日", required) is None
    assert _parse_server_audit_candidate("202年8月9日", required) == required
    assert _parse_server_audit_candidate("20年3月2日", date(2025, 3, 2)) == date(
        2025, 3, 2
    )


def test_unique_server_mobile_otsu_candidate_is_strict_and_conflict_free():
    expected = date(2025, 5, 20)
    assert _unique_server_mobile_otsu_candidate(
        ["2025年5月20日", "2025年5月20日"],
        ["2025年5月20日"],
        ["2025年5月70日", "2025年5月20日"],
    ) == expected
    assert _unique_server_mobile_otsu_candidate(
        ["2025年5月20日"],
        ["2025年5月2日"],
        ["2025年5月20日"],
    ) is None
    assert _unique_server_mobile_otsu_candidate(
        ["2025年5月20日"],
        ["2025年5月20日"],
        ["2025年5月20日", "2025年5月7日"],
    ) is None
    assert _unique_server_mobile_otsu_candidate(
        ["202年5月20日"],
        ["2025年5月20日"],
        [],
    ) is None


def test_single_paddle_date_route_generates_otsu_preview_without_cross_model_candidate(
    tmp_path, monkeypatch,
):
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    called_paths = []

    def line_ocr(path, **_kwargs):
        called_paths.append(str(path))
        return [TextObservation("2025年5月20日", .98, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    _rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source,
        .47,
        "2025-05-20",
        tmp_path / "artifacts",
        "/x",
        "paddle",
        secondary_ocr_backend=None,
    )

    assert artifacts
    assert all(
        artifact.get("date_line_otsu_upscaled_url")
        for artifact in artifacts
    )
    assert not any("otsu-upscaled" in path for path in called_paths)
    assert not any(
        "Otsu 三倍放大 Mobile/Server" in variant.get("preprocessing", "")
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
    )


def test_compact_full_date_audit_requires_all_digits_and_terminal_day():
    from datetime import date

    from receipt_ocr.analyzer import _parse_compact_full_date_audit_candidate

    assert _parse_compact_full_date_audit_candidate("2025年1218日") == date(
        2025, 12, 18
    )
    assert _parse_compact_full_date_audit_candidate("20251218日") == date(
        2025, 12, 18
    )
    assert _parse_compact_full_date_audit_candidate("订单20251218") is None
    assert _parse_compact_full_date_audit_candidate("2025年121日") is None
    assert _parse_compact_full_date_audit_candidate("20250230日") is None


def test_autocontrast_compact_date_is_visible_but_never_reliable(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        if (
            model_variant == "mobile"
            and "tight-line-autocontrast-upscaled" in str(path)
        ):
            return [TextObservation("2025年1218日", .98, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-12-18", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-12-18")
    assert actual == date(2025, 12, 18)
    assert estimate_date_confidence(rows, "2025-12-18", actual) < .72
    assert all(
        artifact.get("date_line_autocontrast_upscaled_url")
        for artifact in artifacts
    )
    assert any(
        "2025年1218日" in variant.get("accepted_texts", [])
        and "完整8位合法日期" in variant.get("acceptance_note", "")
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
    )


def test_max_channel_date_requires_cross_model_cross_geometry_consensus(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if (
            model_variant == "server"
            and "tight-line-max-channel-upscaled" in path
        ):
            return [TextObservation("2025年8月27日", .87, 0, 0, 1, 1)]
        if (
            model_variant == "mobile"
            and "wide-line-max-channel-upscaled" in path
        ):
            return [TextObservation("2025年8月27日", .81, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-08-27", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-08-27")
    assert actual == date(2025, 8, 27)
    assert estimate_date_confidence(rows, "2025-08-27", actual) >= .72
    assert all(
        artifact.get("date_line_max_channel_upscaled_url")
        for artifact in artifacts
    )
    accepted = [
        variant
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
        if "最大通道去彩色" in variant.get("preprocessing", "")
        and variant.get("accepted_texts")
    ]
    assert len(accepted) == 2
    assert all("紧/宽不同裁剪" in item["acceptance_note"] for item in accepted)


def test_max_channel_compact_mobile_date_needs_strict_server_other_geometry(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if (
            model_variant == "mobile"
            and "wide-line-max-channel-upscaled" in path
        ):
            return [TextObservation("2025年1218日", .85, 0, 0, 1, 1)]
        if (
            model_variant == "server"
            and "tight-line-max-channel-upscaled" in path
        ):
            return [TextObservation("2025年12月18日", .89, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-12-18", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-12-18")
    assert actual == date(2025, 12, 18)
    assert estimate_date_confidence(rows, "2025-12-18", actual) >= .72
    accepted = [
        variant
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
        if "最大通道去彩色" in variant.get("preprocessing", "")
        and variant.get("accepted_texts")
    ]
    assert len(accepted) == 2
    assert any(
        "2025年1218日" in item["accepted_texts"]
        and "自包含8位日期" in item["acceptance_note"]
        for item in accepted
    )


def test_max_channel_compact_mobile_date_rejects_same_geometry_server(
    tmp_path, monkeypatch,
):
    from PIL import Image

    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if "wide-line-max-channel-upscaled" not in path:
            return []
        text = (
            "2025年1218日"
            if model_variant == "mobile"
            else "2025年12月18日"
        )
        return [TextObservation(text, .90, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, _artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-12-18", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-12-18")
    assert actual is None


def test_max_channel_date_rejects_same_geometry_or_conflicting_date(
    tmp_path, monkeypatch,
):
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if "wide-line-max-channel-upscaled" in path:
            return [TextObservation("2025年8月27日", .90, 0, 0, 1, 1)]
        if (
            model_variant == "mobile"
            and "tight-line-max-channel-upscaled" in path
        ):
            return [TextObservation("2025年8月21日", .90, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-08-27", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-08-27")
    assert actual is None or estimate_date_confidence(
        rows, "2025-08-27", actual
    ) < .72
    max_variants = [
        variant
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
        if "最大通道去彩色" in variant.get("preprocessing", "")
    ]
    assert max_variants
    assert all(not item.get("accepted_texts") for item in max_variants)
    assert any(
        "存在其他可解析日期" in item.get("acceptance_note", "")
        for item in max_variants
    )


def test_max_channel_three_cell_consensus_tolerates_only_one_digit_day_truncation(
    tmp_path, monkeypatch,
):
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if "max-channel-upscaled" not in path:
            return []
        if model_variant == "mobile":
            return [TextObservation("2025年11月11日", .94, 0, 0, 1, 1)]
        if "wide-line" in path:
            return [TextObservation("2025年11月11日", .93, 0, 0, 1, 1)]
        return [TextObservation("2025年11月1日", .92, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-11-11", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-11-11")
    assert actual == date(2025, 11, 11)
    assert estimate_date_confidence(rows, "2025-11-11", actual) >= .72
    assert sum(
        artifact.get("date_max_channel_consensus_candidate") == "2025-11-11"
        for artifact in artifacts
    ) == 2
    accepted = [
        variant
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
        if variant.get("accepted_texts") == ["2025年11月11日"]
    ]
    assert len(accepted) == 3
    assert all("3 个模型×几何单元格" in item["acceptance_note"] for item in accepted)


def test_three_cell_truncation_helper_rejects_weak_or_real_conflicts():
    required = date(2025, 1, 14)

    def item(model, tight, text="2025年1月14日", *, compact=False):
        return {
            "model": model,
            "tight": tight,
            "compact": compact,
            "rows": [TextObservation(text, .95, 0, 0, 1, 1)],
        }

    strong = [
        item("mobile", True),
        item("mobile", False),
        item("server", True),
    ]
    assert len(_cross_model_max_channel_required_with_truncated_conflict(
        strong, {date(2025, 1, 4)}, required
    )) == 3
    assert _cross_model_max_channel_required_with_truncated_conflict(
        strong[:2], {date(2025, 1, 4)}, required
    ) is None
    assert _cross_model_max_channel_required_with_truncated_conflict(
        strong, {date(2025, 1, 13)}, required
    ) is None
    assert _cross_model_max_channel_required_with_truncated_conflict(
        strong, {date(2025, 2, 4)}, required
    ) is None
    assert _cross_model_max_channel_required_with_truncated_conflict(
        strong, {date(2025, 1, 4), date(2025, 1, 3)}, required
    ) is None
    assert _cross_model_max_channel_required_with_truncated_conflict(
        [strong[0], strong[1], item("server", True, compact=True)],
        {date(2025, 1, 4)}, required,
    ) is None


def test_four_cell_mismatch_candidate_accepts_only_one_literal_day_truncation():
    required = date(2025, 3, 14)
    candidate = date(2025, 3, 13)

    def item(model, tight, value=candidate, *, compact=False):
        return {
            "model": model,
            "tight": tight,
            "date": value,
            "compact": compact,
            "rows": [TextObservation(
                f"{value.year}年{value.month}月{value.day}日",
                .95, 0, 0, 1, 1,
            )],
        }

    strong = [
        item("mobile", True), item("mobile", False),
        item("server", True), item("server", False),
    ]
    rows = [
        TextObservation("2025年3月13日", .95, 0, 0, 1, 1),
        TextObservation("2025年3月3日", .90, 0, 0, 1, 1),
        # A damaged year is not a literal full-date conflict.
        TextObservation("202年2月12日", .90, 0, 0, 1, 1),
    ]

    accepted = _max_channel_truncated_mismatch_candidate(
        strong, rows, required
    )
    assert accepted is not None
    assert accepted[0] == candidate
    assert len(accepted[1]) == 4
    no_conflict = _max_channel_truncated_mismatch_candidate(
        strong, [rows[0], rows[2]], required
    )
    assert no_conflict is not None and no_conflict[0] == candidate
    assert _max_channel_truncated_mismatch_candidate(
        strong[:3], rows, required
    ) is None
    assert _max_channel_truncated_mismatch_candidate(
        strong,
        rows + [TextObservation("2025年3月12日", .9, 0, 0, 1, 1)],
        required,
    ) is None
    assert _max_channel_truncated_mismatch_candidate(
        strong,
        [rows[0], TextObservation("2025年2月3日", .9, 0, 0, 1, 1)],
        required,
    ) is None


def test_day_slot_requires_mobile_server_and_both_preprocessings():
    def variants(value="13"):
        return [
            {
                "model": model,
                "preprocessing": preprocessing,
                "parsed_components": [value],
            }
            for model in ("mobile", "server")
            for preprocessing in (
                "最大通道去彩色", "最大通道去彩色并去横线"
            )
        ]

    strong = variants()
    assert _day_slot_confirms_value(strong, 13)
    assert not _day_slot_confirms_value(strong[:3], 13)
    strong[-1]["parsed_components"] = ["3"]
    assert not _day_slot_confirms_value(strong, 13)


def _white_day_conflict_artifacts():
    return [
        {
            "variant": geometry,
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_variants": [
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 Mobile 跨几何复核"
                    ),
                    "ocr_texts": ["2025年8月22月"],
                },
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 Server 跨几何复核"
                    ),
                    "ocr_texts": ["2025年8月23月"],
                },
            ],
        }
        for geometry in ("紧凑区域", "宽区域")
    ]


def test_white_day_conflict_prefilter_requires_hybrid_two_geometries():
    assert _parse_full_year_month_day_audit(
        "2025年8月22月"
    ) == date(2025, 8, 22)
    assert _parse_full_year_month_day_audit("20年8月22日") is None

    artifacts = _white_day_conflict_artifacts()
    selected = _white_day_conflict_prefilter_from_artifacts(
        artifacts, "2025-08-23"
    )
    assert selected is not None
    assert selected["date"] == date(2025, 8, 22)
    assert selected["conflict"] == date(2025, 8, 23)
    assert _white_day_conflict_prefilter_from_artifacts(
        artifacts[:1], "2025-08-23"
    ) is None
    artifacts[0]["ocr_backend"] = "PaddleOCR PP-OCRv5 Mobile"
    assert _white_day_conflict_prefilter_from_artifacts(
        artifacts, "2025-08-23"
    ) is None


def test_white_day_conflict_candidate_stays_component_strict():
    prefilter = _white_day_conflict_prefilter_from_artifacts(
        _white_day_conflict_artifacts(), "2025-08-23"
    )
    variants = [
        {
            "slot": "整行冲突完整年份槽位",
            "model": model,
            "preprocessing": "最大通道去彩色",
            "ocr_texts": ["2025年8"],
        }
        for model in ("mobile", "server")
    ] + [
        {
            "slot": "整行冲突白边日上下文槽位",
            "model": model,
            "preprocessing": preprocessing,
            "ocr_texts": ["月22日" if model == "server" else "月22月"],
        }
        for model in ("mobile", "server")
        for preprocessing in ("白边标准化", "裁后白边标准化")
    ]
    assert _white_day_conflict_audit_candidate(
        prefilter, variants
    ) == date(2025, 8, 22)
    variants[-1]["ocr_texts"] = ["月23日"]
    assert _white_day_conflict_audit_candidate(prefilter, variants) is None


def _partial_year_day_before_artifacts():
    values = {
        ("mobile", "紧凑区域"): "200年8月19日",
        ("server", "紧凑区域"): "202年8月19日",
        ("mobile", "宽区域"): "20年8月19日",
        ("server", "宽区域"): "200年8月19日",
    }
    return [
        {
            "variant": geometry,
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_variants": [
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 "
                        f"{model.title()} 跨几何复核"
                    ),
                    "ocr_texts": [values[(model, geometry)]],
                }
                for model in ("mobile", "server")
            ],
        }
        for geometry in ("紧凑区域", "宽区域")
    ]


def test_partial_year_day_before_audit_requires_four_hybrid_cells():
    required = date(2025, 8, 20)
    assert _parse_partial_year_month_day_audit(
        "200年8月19日", required
    ) == date(2025, 8, 19)
    assert _parse_partial_year_month_day_audit(
        "2025年8月19日", required
    ) is None

    artifacts = _partial_year_day_before_artifacts()
    selected = _partial_year_day_before_audit_from_artifacts(
        artifacts, "2025-08-20", "2025-08-16"
    )
    assert selected is not None
    assert selected["date"] == date(2025, 8, 19)
    assert set(selected["support"]) == {"mobile", "server"}

    assert _partial_year_day_before_audit_from_artifacts(
        artifacts[:1], "2025-08-20", "2025-08-16"
    ) is None
    artifacts[0]["ocr_backend"] = "PaddleOCR PP-OCRv5 Mobile"
    assert _partial_year_day_before_audit_from_artifacts(
        artifacts, "2025-08-20", "2025-08-16"
    ) is None


@pytest.mark.parametrize(
    ("mutation", "required", "creation"),
    [
        ("wrong_day", "2025-08-20", "2025-08-16"),
        ("complete_year", "2025-08-20", "2025-08-16"),
        ("required_not_next_day", "2025-08-21", "2025-08-16"),
        ("before_creation", "2025-08-20", "2025-08-20"),
        ("single_digit_day", "2025-08-10", "2025-08-01"),
    ],
)
def test_partial_year_day_before_audit_rejects_unsafe_variants(
    mutation, required, creation,
):
    artifacts = _partial_year_day_before_artifacts()
    if mutation == "wrong_day":
        artifacts[1]["date_line_ocr_variants"][1]["ocr_texts"] = [
            "200年8月18日"
        ]
    elif mutation == "complete_year":
        artifacts[1]["date_line_ocr_variants"][1]["ocr_texts"] = [
            "2025年8月19日"
        ]
    elif mutation == "single_digit_day":
        for artifact in artifacts:
            for variant in artifact["date_line_ocr_variants"]:
                variant["ocr_texts"] = ["20年8月9日"]
    assert _partial_year_day_before_audit_from_artifacts(
        artifacts, required, creation
    ) is None


def test_missing_year_separator_parser_requires_all_literal_components():
    assert _parse_missing_year_separator_full_date(
        "20256月16日"
    ) == date(2025, 6, 16)
    assert _parse_missing_year_separator_full_date("2025年6月16日") is None
    assert _parse_missing_year_separator_full_date(
        "20546月16日"
    ) == date(2054, 6, 16)
    assert _parse_missing_year_separator_full_date("20256月32日") is None


def test_missing_year_separator_consensus_keeps_platform_and_conflict_guards():
    artifact = {
        "variant": "紧凑区域",
        "ocr_backend": "macOS Vision",
        "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_line_ocr_variants": [
            {
                "preprocessing": "日期行原图",
                "ocr_texts": ["20256月16日"],
            },
            {
                "preprocessing": "日期行去表格线",
                "ocr_texts": ["2025年6月6日"],
            },
            {
                "preprocessing": "日期行 Server 大模型复核",
                "ocr_texts": ["2025年6月16日"],
            },
        ],
    }
    accepted = _missing_year_separator_consensus_from_artifacts(
        [artifact], "2025-06-16"
    )
    assert accepted is not None and accepted["date"] == date(2025, 6, 16)
    assert accepted["other_dates"] == ["2025-06-06"]

    assert _missing_year_separator_consensus_from_artifacts(
        [{**artifact, "ocr_backend": "PaddleOCR PP-OCRv5 Mobile"}],
        "2025-06-16",
    ) is None
    assert _missing_year_separator_consensus_from_artifacts(
        [artifact], "2025-06-15"
    ) is None
    wrong_literal_year = {
        **artifact,
        "date_line_ocr_variants": [
            {
                "preprocessing": "日期行原图",
                "ocr_texts": ["20546月16日"],
            },
            {
                "preprocessing": "日期行 Server 大模型复核",
                "ocr_texts": ["2054年6月16日"],
            },
        ],
    }
    assert _missing_year_separator_consensus_from_artifacts(
        [wrong_literal_year], "2025-06-16"
    ) is None
    conflicting = {
        **artifact,
        "date_line_ocr_variants": artifact["date_line_ocr_variants"] + [{
            "preprocessing": "日期行灰度自动对比 Server 人工候选",
            "ocr_texts": ["2025年6月12日"],
        }],
    }
    assert _missing_year_separator_consensus_from_artifacts(
        [conflicting], "2025-06-16"
    ) is None


def _cross_year_nondestructive_artifacts() -> list[dict]:
    artifacts = []
    for variant, server_date in (
        ("紧凑区域", "2025年5月24日"),
        ("宽区域", "2026年5月24日"),
    ):
        artifacts.append({
            "variant": variant,
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "secondary_ocr_variants": [
                {"preprocessing": "原始裁剪", "ocr_texts": ["2026年5月24日"]},
                {"preprocessing": "去印章色", "ocr_texts": ["2026年5月24日"]},
            ],
            "date_line_ocr_variants": [
                {"preprocessing": "日期行原图", "ocr_texts": ["2026年5月24日"]},
                {"preprocessing": "日期行去印章色", "ocr_texts": ["2026年5月24日"]},
                {
                    "preprocessing": "日期行 Server 大模型复核不一致日期",
                    "ocr_texts": [server_date],
                },
                {
                    "preprocessing": "日期行 Server 大模型低置信度候选",
                    "ocr_texts": [server_date],
                },
            ],
        })
    return artifacts


def test_cross_year_nondestructive_consensus_requires_all_literal_cells():
    accepted = _cross_year_nondestructive_consensus_from_artifacts(
        _cross_year_nondestructive_artifacts(), "2025-05-24"
    )
    assert accepted is not None
    assert accepted["date"] == date(2026, 5, 24)
    assert accepted["support"]["other_dates"] == ["2025-05-24"]

    missing = _cross_year_nondestructive_artifacts()
    missing[1]["secondary_ocr_variants"][0]["ocr_texts"] = []
    assert _cross_year_nondestructive_consensus_from_artifacts(
        missing, "2025-05-24"
    ) is None

    one_server_cell_per_geometry = _cross_year_nondestructive_artifacts()
    for artifact in one_server_cell_per_geometry:
        artifact["date_line_ocr_variants"] = [
            item for item in artifact["date_line_ocr_variants"]
            if item["preprocessing"]
            != "日期行 Server 大模型低置信度候选"
        ]
    assert _cross_year_nondestructive_consensus_from_artifacts(
        one_server_cell_per_geometry, "2025-05-24"
    )["date"] == date(2026, 5, 24)

    no_wide_server = _cross_year_nondestructive_artifacts()
    no_wide_server[1]["date_line_ocr_variants"] = [
        item for item in no_wide_server[1]["date_line_ocr_variants"]
        if "Server" not in item["preprocessing"]
    ]
    assert _cross_year_nondestructive_consensus_from_artifacts(
        no_wide_server, "2025-05-24"
    ) is None


def test_cross_year_nondestructive_consensus_keeps_platform_and_date_guards():
    artifacts = _cross_year_nondestructive_artifacts()
    artifacts[0]["ocr_backend"] = "PaddleOCR PP-OCRv5 Mobile"
    assert _cross_year_nondestructive_consensus_from_artifacts(
        artifacts, "2025-05-24"
    ) is None

    conflict = _cross_year_nondestructive_artifacts()
    conflict[1]["date_line_ocr_variants"].append({
        "preprocessing": "日期行去表格线",
        "ocr_texts": ["2026年4月24日"],
    })
    assert _cross_year_nondestructive_consensus_from_artifacts(
        conflict, "2025-05-24"
    ) is None

    assert _cross_year_nondestructive_consensus_from_artifacts(
        _cross_year_nondestructive_artifacts(), "2025-05-23"
    ) is None


def test_max_channel_cross_model_cross_geometry_confirms_strict_mismatch(
    tmp_path, monkeypatch,
):
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if (
            model_variant == "mobile"
            and "wide-line-max-channel-upscaled" in path
        ) or (
            model_variant == "server"
            and "tight-line-max-channel-upscaled" in path
        ):
            return [TextObservation("2025年6月19日", .91, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-06-20", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-06-20")
    assert actual == date(2025, 6, 19)
    assert estimate_date_confidence(rows, "2025-06-20", actual) >= .72
    assert sum(
        artifact.get("date_max_channel_mismatch_candidate") == "2025-06-19"
        for artifact in artifacts
    ) == 2
    accepted = [
        variant
        for artifact in artifacts
        for variant in artifact["date_line_ocr_variants"]
        if variant.get("accepted_texts") == ["2025年6月19日"]
    ]
    assert len(accepted) == 2
    assert all("完整不匹配日期" in item["acceptance_note"] for item in accepted)


def test_max_channel_mismatch_rejects_same_geometry_or_other_strict_date(
    tmp_path, monkeypatch,
):
    from PIL import Image

    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def same_geometry(path, *, model_variant="mobile", **_kwargs):
        if "wide-line-max-channel-upscaled" in str(path):
            return [TextObservation("2025年6月19日", .91, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", same_geometry)
    rows, _ = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-06-20", tmp_path / "same", "/x",
        "vision", secondary_ocr_backend="paddle",
    )
    assert find_receipt_date(rows, "2025-06-20")[0] is None

    def conflicting(path, *, model_variant="mobile", **_kwargs):
        path = str(path)
        if model_variant == "mobile" and "wide-line-max-channel-upscaled" in path:
            return [
                TextObservation("2025年6月19日", .91, 0, 0, 1, .4),
                TextObservation("2025年6月18日", .90, 0, .5, 1, .4),
            ]
        if model_variant == "server" and "tight-line-max-channel-upscaled" in path:
            return [TextObservation("2025年6月19日", .92, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", conflicting)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-06-20", tmp_path / "conflict", "/y",
        "vision", secondary_ocr_backend="paddle",
    )
    assert find_receipt_date(rows, "2025-06-20")[0] is None
    assert all(
        not artifact.get("date_max_channel_mismatch_candidate")
        for artifact in artifacts
    )


def test_fixed_template_slots_expose_cross_model_candidate_but_never_reliable(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        if "date-slot-month_day-max-channel" in str(path):
            return [TextObservation("年8月27日", .96, 0, 0, 1, 1)]
        return []

    def detector_ocr(path, *, model_variant="mobile", **_kwargs):
        if "date-slot-year_full-max-channel" in str(path):
            return [TextObservation("2025年", .94, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_text", detector_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-08-27", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-08-27")
    assert actual == date(2025, 8, 27)
    assert estimate_date_confidence(rows, "2025-08-27", actual) < .72
    tight = next(item for item in artifacts if item["variant"] == "紧凑区域")
    assert tight["date_slot_candidate"] == "2025-08-27"
    assert tight["date_slot_year_original_url"].endswith(
        "date-slot-year_full-original.jpg"
    )
    assert tight["date_slot_month_day_processed_url"].endswith(
        "date-slot-month_day-max-channel.png"
    )
    assert tight["date_slot_month_digit_original_url"].endswith(
        "date-slot-month_digits-original.jpg"
    )
    assert tight["date_slot_month_digit_processed_url"].endswith(
        "date-slot-month_digits-max-channel.png"
    )
    assert "仅供人工复核" in tight["date_slot_acceptance_note"]


def test_fixed_template_slots_promote_only_strict_required_date_consensus(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr(
        "receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: []
    )

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        name = str(path)
        if "date-slot-year_full-max-channel-line-clean" in name:
            return [TextObservation("2025年4", .94, 0, 0, 1, 1)]
        if (
            model_variant == "server"
            and "date-slot-year_full-max-channel.png" in name
        ):
            return [TextObservation("2025年4", .96, 0, 0, 1, 1)]
        if "date-slot-month_day-max-channel" in name:
            return [TextObservation("年4月3日", .95, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_text", lambda *_a, **_k: []
    )
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-04-03", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-04-03")
    assert actual == date(2025, 4, 3)
    assert estimate_date_confidence(rows, "2025-04-03", actual) >= .72
    tight = next(item for item in artifacts if item["variant"] == "紧凑区域")
    assert tight["date_slot_reliable"] is True
    assert tight["date_slot_candidate"] == "2025-04-03"
    assert tight["date_slot_year_line_clean_url"].endswith(
        "date-slot-year_full-max-channel-line-clean.png"
    )
    assert tight["date_slot_month_day_line_clean_url"].endswith(
        "date-slot-month_day-max-channel-line-clean.png"
    )
    assert "可自动核验" in tight["date_slot_acceptance_note"]


def test_cross_model_slot_required_date_rejects_mismatch_or_conflict():
    years = [
        {"model": "mobile", "ocr_texts": ["2025年4"]},
        {"model": "server", "ocr_texts": ["2025年"]},
    ]
    month_days = [
        {"model": "mobile", "ocr_texts": ["年4月3日"]},
        {"model": "server", "ocr_texts": ["4月3日"]},
    ]
    assert _cross_model_slot_required_date(
        years, month_days, "2025-04-03", ["20年4月3日"]
    ) == date(2025, 4, 3)
    assert _cross_model_slot_required_date(
        years, month_days, "2025-04-04", []
    ) is None
    assert _cross_model_slot_required_date(
        years, month_days, "2025-04-03", ["2025年4月2日"]
    ) is None
    assert _cross_model_slot_required_date(
        years[:1], month_days, "2025-04-03", []
    ) is None


def test_vision_month_day_slot_exposes_only_low_confidence_review_candidate(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        if "date-slot-year_full-max-channel-white" in str(path):
            return [TextObservation("2025年", .95, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    monkeypatch.setattr(
        "receipt_ocr.analyzer._recognize_date_slot_with_vision",
        lambda *_a, **_k: [TextObservation("年8月24日", .88, 0, 0, 1, 1)],
    )
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-08-25", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-08-25")
    assert actual == date(2025, 8, 24)
    assert estimate_date_confidence(rows, "2025-08-25", actual) < .72
    tight = next(item for item in artifacts if item["variant"] == "紧凑区域")
    assert tight["date_slot_candidate"] == "2025-08-24"
    assert tight["date_slot_candidate_source"] == (
        "Paddle 双模型年份 + Vision 单路径月日"
    )
    assert tight["date_slot_year_white_url"].endswith(
        "date-slot-year_full-max-channel-white.png"
    )
    assert tight["date_slot_month_day_vision_url"].endswith(
        "date-slot-month_day-crop-first-max-channel-white.png"
    )
    assert "必须人工复核" in tight["date_slot_acceptance_note"]


def test_vision_month_day_slot_rejects_disagreeing_paddle_years(
    tmp_path, monkeypatch,
):
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, *, model_variant="mobile", **_kwargs):
        if "date-slot-year_full-max-channel-white" in str(path):
            year = "2025年" if model_variant == "mobile" else "2024年"
            return [TextObservation(year, .95, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    monkeypatch.setattr(
        "receipt_ocr.analyzer._recognize_date_slot_with_vision",
        lambda *_a, **_k: [TextObservation("年8月24日", .88, 0, 0, 1, 1)],
    )
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-08-25", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    assert not any("2025年8月24日" == row.text for row in rows)
    tight = next(item for item in artifacts if item["variant"] == "紧凑区域")
    assert tight["date_slot_candidate"] == ""
    assert "未形成" in tight["date_slot_acceptance_note"]


def test_vision_original_white_line_exposes_stable_review_candidate(
    tmp_path, monkeypatch,
):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import estimate_date_confidence, find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line", lambda *_a, **_k: []
    )
    variants = [{
        "preprocessing": "原日期行白底标准化 Vision 中英关闭纠错 人工候选",
        "ocr_texts": ["_2025年3月7E"],
        "accepted_texts": ["_2025年3月7E"],
        "acceptance_note": "三种配置一致，仅供人工复核",
    }]
    monkeypatch.setattr(
        "receipt_ocr.analyzer._recognize_date_line_vision_consensus",
        lambda *_a, **_k: (variants, {date(2025, 3, 7)}),
    )
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-03-07", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-03-07")
    assert actual == date(2025, 3, 7)
    assert estimate_date_confidence(rows, "2025-03-07", actual) < .72
    tight = next(item for item in artifacts if item["variant"] == "紧凑区域")
    assert tight["date_slot_candidate"] == ""
    assert tight["date_line_white_candidate"] == "2025-03-07"
    assert tight["date_line_white_standardized_url"].endswith(
        "date-tight-line-original-white-standardized.png"
    )
    assert "置信度封顶 20%" in tight["date_line_white_acceptance_note"]
    assert any(
        item.get("preprocessing", "").startswith("原日期行白底标准化")
        for item in tight["date_line_ocr_variants"]
    )


def test_business_rejected_date_evidence_keeps_original_ocr_texts():
    from receipt_ocr.analyzer import (
        _collect_business_rejected_date_evidence,
    )

    rows = [
        TextObservation("2025年3月2日", .81, 0, 0, 1, 1),
        TextObservation("2025年3月7日", .20, 0, 0, 1, 1),
        TextObservation("2025年3月2日", .33, 0, 0, 1, 1),
    ]
    rejected = _collect_business_rejected_date_evidence(
        rows, "2025-03-07", "2025-03-04", "W20250304-008104"
    )

    assert rejected == [{
        "value": "2025-03-02",
        "ocr_texts": ["2025年3月2日"],
        "reason": "早于最早出库业务日期 2025-03-04",
    }]


def test_fixed_template_slot_parsers_require_self_contained_components():
    from receipt_ocr.analyzer import (
        _parse_date_slot_month_day,
        _parse_date_slot_year,
    )

    assert _parse_date_slot_year("2025年8") == 2025
    assert _parse_date_slot_year("25年8") is None
    assert _parse_date_slot_year("20258") is None
    assert _parse_date_slot_month_day("年8月27日") == (8, 27)
    assert _parse_date_slot_month_day("0802") is None
    assert _parse_date_slot_month_day("13月40日") is None




def test_mobile_and_server_other_geometry_can_confirm_strict_date(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    from receipt_ocr.analyzer import ReceiptAnalyzer
    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, model_variant="mobile", **_kwargs):
        name = str(path)
        if model_variant == "mobile":
            text = "2025年11月1日" if "wide-line-original" in name else "2025年11月日"
        else:
            text = "2025年11月1日" if "tight-line-original" in name else "202年11月日"
        return [TextObservation(text, .93, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-11-01", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-11-01")
    assert actual == date(2025, 11, 1)
    assert any(
        item.get("acceptance_note")
        == "Mobile 与 Server 在紧/宽不同裁剪上读到同一完整日期"
        for artifact in artifacts for item in artifact["date_line_ocr_variants"]
    )


def test_low_confidence_matching_candidate_still_gets_cross_geometry_confirmation(
    tmp_path, monkeypatch,
):
    from PIL import Image

    source = tmp_path / "receipt-low-confidence-date.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)

    def region_ocr(path, *, backend, **_kwargs):
        if backend == "paddle" and "date-wide-original" in str(path):
            return [TextObservation("202年11月23日", .95, .80, .50, .15, .10)]
        return []

    def line_ocr(path, model_variant="mobile", **_kwargs):
        name = str(path)
        if model_variant == "mobile" and "wide-line-original" in name:
            return [TextObservation("2025年11月23日", .63, 0, 0, 1, 1)]
        if model_variant == "server" and "tight-line-original" in name:
            return [TextObservation("2025年11月23日", .94, 0, 0, 1, 1)]
        return [TextObservation("2025年11月日", .90, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", region_ocr)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-11-23", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, evidence = find_receipt_date(rows, "2025-11-23")
    assert actual is not None
    assert actual.isoformat() == "2025-11-23"
    assert evidence is not None
    assert any(
        parse_date(row.text) == actual and row.confidence == .94
        for row in rows
    )
    assert any(
        item.get("acceptance_note")
        == "Mobile 与 Server 在紧/宽不同裁剪上读到同一完整日期"
        for artifact in artifacts for item in artifact["date_line_ocr_variants"]
    )


def test_far_below_handwritten_date_is_visible_as_audit_candidate_only(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)

    def fake_save_crop(_source, destination, _anchor_y, *, tight, raw_destination, color_clean_destination, left=None):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        if "date-far_lower" in str(destination):
            assert left == .48
        return (.72, .55, .27, .10)

    def fake_recognize_text(path, *, custom_words=None, **_kwargs):
        if "date-far_lower-original" in str(path):
            assert custom_words == []
            return [TextObservation("2025.8.5", .99, .10, .10, .70, .30)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_recognize_text)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", lambda *_a, **_k: [])

    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-08-05", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, evidence = find_receipt_date(rows, "2025-08-05")
    assert actual == date(2025, 8, 5)
    assert evidence is not None and evidence.confidence == .35
    assert any(item["variant"] == "远下方手写日期复核区域" for item in artifacts)


def test_far_lower_date_needs_repeated_cross_model_cross_geometry_evidence(
    tmp_path, monkeypatch
):
    from datetime import date
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)

    def fake_save_crop(
        _source, destination, _anchor_y, *, tight, raw_destination,
        color_clean_destination, left=None
    ):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        return (.48, .70, .515, .08)

    def fake_recognize_text(path, *, backend, **_kwargs):
        name = path.name
        if backend == "paddle" and name in {
            "date-far_lower-original.jpg",
            "date-far_lower-color-clean.png",
        }:
            return [TextObservation("之2025.8.12", .94, 0, 0, 1, 1)]
        if backend == "paddle_server" and name in {
            "date-far_lower-line-original.jpg",
            "date-far_lower-line-color-clean.png",
        }:
            return [TextObservation("之2025.8.12", .96, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr(
        "receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop
    )
    monkeypatch.setattr(
        "receipt_ocr.analyzer.recognize_text", fake_recognize_text
    )
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line", lambda *_a, **_k: []
    )

    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source,
        .47,
        "2025-08-12",
        tmp_path / "artifacts",
        "/x",
        "vision",
        secondary_ocr_backend="paddle",
    )

    actual, _ = _find_confirmed_far_lower_date(rows, artifacts)
    assert actual == date(2025, 8, 12)
    assert estimate_date_confidence(rows, "2025-08-12", actual) >= .72
    far = next(
        item for item in artifacts
        if item["variant"] == "远下方手写日期复核区域"
    )
    assert far["far_lower_cross_model_candidate"] == "2025-08-12"
    assert far["far_lower_server_backend"].endswith("Server（大模型）")
    assert any(
        "不同几何" in item.get("acceptance_note", "")
        for item in far["date_line_ocr_variants"]
    )

    single_rows, single_artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source,
        .47,
        "2025-08-12",
        tmp_path / "single-artifacts",
        "/single",
        "paddle",
        secondary_ocr_backend=None,
    )
    single_actual, single_evidence = _find_low_confidence_date_audit(
        single_rows
    )
    assert single_actual == date(2025, 8, 12)
    assert single_evidence is not None and single_evidence.confidence == .35
    assert not any(
        item.get("far_lower_cross_model_candidate")
        for item in single_artifacts
    )


def test_far_lower_cross_model_helper_rejects_one_view_or_conflict():
    mobile = [
        {"preprocessing": "原始裁剪", "ocr_texts": ["2025.8.12"]},
        {"preprocessing": "去印章色", "ocr_texts": ["2025.8.12"]},
    ]
    server = [
        {"preprocessing": "窄日期行原图", "ocr_texts": ["2025.8.12"]},
        {"preprocessing": "窄日期行去印章色", "ocr_texts": ["2025.8.12"]},
    ]
    assert _cross_model_far_lower_strict_date(
        mobile, server, ["2025.8.12"]
    ).isoformat() == "2025-08-12"
    assert _cross_model_far_lower_strict_date(
        mobile, server[:1], ["2025.8.12"]
    ) is None
    assert _cross_model_far_lower_strict_date(
        mobile, server, ["2025.8.12", "2035.8.12"]
    ) is None


def test_low_confidence_component_audit_allows_only_one_required_date():
    from receipt_ocr.ocr_types import TextObservation

    def row(text: str, confidence: float = 0.68) -> TextObservation:
        return TextObservation(text, confidence, 0.8, 0.6, 0.1, 0.03)

    assert _needs_low_confidence_date_audit([], "2025-02-11")
    assert _needs_low_confidence_date_audit(
        [row("202年2月11日")], "2025-02-11"
    )
    assert not _needs_low_confidence_date_audit(
        [row("2025年2月11日", 0.90)], "2025-02-11"
    )
    assert not _needs_low_confidence_date_audit(
        [row("202年2月11日"), row("2025年2月2日")], "2025-02-11"
    )
    assert not _needs_low_confidence_date_audit(
        [row("202年2月10日")], "2025-02-11"
    )


def test_page_bottom_handwritten_date_is_visible_but_never_auto_reliable(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)

    def fake_save_crop(_source, destination, _anchor_y, *, tight, raw_destination, color_clean_destination, left=None):
        for path in (destination, raw_destination, color_clean_destination):
            Image.new("RGB", (500, 150), "white").save(path)
        if "date-deep_lower" in str(destination):
            assert left == .48
        return (.48, .83, .515, .06)

    def fake_recognize_text(path, *, custom_words=None, **_kwargs):
        if "date-deep_lower" in str(path):
            assert custom_words == []
            return [TextObservation("2025.11.18", .99, .10, .10, .70, .30)]
        return []

    monkeypatch.setattr("receipt_ocr.analyzer.save_receipt_date_crop", fake_save_crop)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", fake_recognize_text)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", lambda *_a, **_k: [])

    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-11-18", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, evidence = _find_low_confidence_date_audit(rows)
    assert actual == date(2025, 11, 18)
    assert evidence is not None and evidence.confidence == .35
    assert any(item["variant"] == "页面底部手写日期复核区域" for item in artifacts)


def test_two_geometry_partial_dates_need_strict_server_confirmation(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, model_variant="mobile", **_kwargs):
        name = str(path)
        if model_variant == "server":
            text = "2025年3月1日" if "tight" in name else "2025年3月日"
        else:
            text = "202年3月1" if "original" in name else "202年2月"
        return [TextObservation(text, .88, 0, 0, 1, 1)]

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-03-01", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-03-01")
    assert actual == date(2025, 3, 1)
    assert any(
        item.get("acceptance_note") == "紧裁/宽裁月日一致 + Server 完整日期复核"
        for artifact in artifacts for item in artifact["date_line_ocr_variants"]
    )


def test_upper_signature_row_date_needs_mobile_and_server_agreement(tmp_path, monkeypatch):
    from datetime import date
    from PIL import Image

    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, model_variant="mobile", **_kwargs):
        if "upper-line" in str(path):
            return [TextObservation("2025.10.20", .93, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-10-20", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, evidence = find_receipt_date(rows, "2025-10-20")
    assert actual == date(2025, 10, 20)
    assert evidence is not None
    wide = next(item for item in artifacts if item["variant"] == "宽区域")
    assert wide["upper_date_line_original_url"].endswith(
        "date-wide-upper-line-original.jpg"
    )
    assert any(
        item.get("acceptance_note") == "上方手写日期经 Mobile 与 Server 独立确认"
        for item in wide["date_line_ocr_variants"]
    )


def test_table_clean_date_line_is_visible_and_partial_text_is_audit_only(tmp_path, monkeypatch):
    from PIL import Image

    from receipt_ocr.parser import find_receipt_date

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (1000, 1600), "white").save(source)
    monkeypatch.setattr("receipt_ocr.analyzer.recognize_text", lambda *_a, **_k: [])

    def line_ocr(path, model_variant="mobile", **_kwargs):
        if "line-table-clean" in str(path):
            # This can parse by inheriting the required month, but it is not a
            # complete independently read date and must not be promoted.
            return [TextObservation("20年4日", .96, 0, 0, 1, 1)]
        return []

    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_line", line_ocr)
    rows, artifacts = ReceiptAnalyzer()._recognize_receipt_date(
        source, .47, "2025-01-04", tmp_path / "artifacts", "/x",
        "vision", secondary_ocr_backend="paddle",
    )

    actual, _ = find_receipt_date(rows, "2025-01-04")
    assert actual is None
    tight = next(item for item in artifacts if item["variant"] == "紧凑区域")
    assert tight["date_line_table_clean_url"].endswith(
        "date-tight-line-table-clean.png"
    )
    table_variant = next(
        item for item in tight["date_line_ocr_variants"]
        if item["preprocessing"] == "日期行去表格线"
    )
    assert table_variant["ocr_texts"] == ["20年4日"]
    assert table_variant["accepted_texts"] == []
