"""Standard matching must respect the receiving-form evidence boundary."""
from copy import deepcopy

import pytest

from receipt_ocr.domain.seal_standards import (
    apply_standard_check,
    seal_candidates,
    standard_comparison,
)


REQUIREMENT = "上海朱泾DC021DC0收货专用章①"


def artifact(index=0, *, managed=True, status="conflict", selected="", matching=None, shape="rectangle"):
    result = {
        "index": index, "region_id": f"seal-{index}", "shape_code": shape, "color": "red",
        "original_url": f"/files/artifacts/guard/{index}-original.png",
        "color_isolated_url": f"/files/artifacts/guard/{index}-color.png",
        "reads": [], "matching_texts": [] if matching is None else matching,
    }
    if managed:
        result["receiving_form"] = {"managed": True, "status": status,
                                    "identity_text": selected, "candidate_text": REQUIREMENT,
                                    "reason": "primary_meaningful_conflict"}
    return result


def receipt(*artifacts, requirement=REQUIREMENT):
    return {
        "fields": {"签章要求": requirement, "要求到货": "2026-01-31"},
        "date_check": {"actual": "2026-01-31", "required": "2026-01-31",
                       "status": "匹配", "reliable": True},
        "seal_check": {"requirement": requirement, "recognized": "", "status": "未识别",
                       "reliable": False, "receiving_review": {"required": True}},
        "processing_artifacts": {"seals": list(artifacts)},
        "review_issues": [], "review_reasons": [], "review_status": "待复核",
        "overall": "需人工复核", "final_result": "需人工复核",
    }


def standard(requirement=REQUIREMENT):
    return {"id": 1, "shape": "rectangle", "color": "red", "revision": 1,
            "requirement": requirement}


def test_conflicting_exact_legacy_aggregate_cannot_be_resurrected_by_standard():
    # This previously bypassed the OCR hook: standard registration reevaluated
    # rectangular_text even though all active reads had already been withheld.
    stamp = artifact()
    stamp.update(rectangular_text=REQUIREMENT,
                 reads=[{"channel": "body", "text": REQUIREMENT, "confidence": .99,
                         "matching": False}])
    item = receipt(stamp)
    original = deepcopy(item)
    result = apply_standard_check(item, standard())
    candidate = result["seal_standard_check"]["candidates"][0]
    assert candidate["texts"] == []
    assert not candidate["matched"]
    assert result["seal_standard_check"]["status"] == "unmatched"
    assert result["seal_check"]["status"] != "匹配"
    assert result["seal_check"]["reliable"] is False
    assert result["overall"] != "通过"
    assert item == original


@pytest.mark.parametrize("field,value", [
    ("rectangular_text", REQUIREMENT),
    ("color_isolated_text", REQUIREMENT),
    ("color_isolated_texts", [REQUIREMENT]),
    ("combined_text", REQUIREMENT),
    ("unwrapped_text", REQUIREMENT),
    ("round_type_band_text", REQUIREMENT),
    ("matching_texts", [REQUIREMENT]),
    ("reads", [{"channel": "receiving_split", "text": REQUIREMENT, "confidence": .99,
                "matching": False}]),
])
def test_each_withheld_legacy_or_split_channel_stays_out_of_standard_matching(field, value):
    stamp = artifact()
    stamp[field] = value
    candidate = seal_candidates(receipt(stamp))[0]
    assert candidate["texts"] == []
    assert candidate["text_status"] != "匹配"


def test_withheld_ring_and_center_cannot_be_recombined_within_the_managed_stamp():
    stamp = artifact()
    stamp.update(unwrapped_text="上海朱泾DC021DC0", round_type_band_text="收货专用章①")
    assert seal_candidates(receipt(stamp))[0]["texts"] == []


def test_withheld_ring_serial_cannot_create_an_alternate_exact_candidate():
    requirement = REQUIREMENT + "印章编号1234567890"
    stamp = artifact()
    stamp.update(unwrapped_text="上海朱泾DC021DC0", round_type_band_text="收货专用章①",
                 ring_serial_text="1234567890", ring_serial_detection={"status": "recognized"})
    candidate = seal_candidates(receipt(stamp, requirement=requirement))[0]
    assert candidate["texts"] == []
    assert standard_comparison(receipt(stamp, requirement=requirement), standard(requirement))["status"] == "unmatched"


@pytest.mark.parametrize("state,identity,matching", [
    ("conflict", REQUIREMENT, [REQUIREMENT]),
    ("insufficient", REQUIREMENT, [REQUIREMENT]),
    ("adopted", "", [REQUIREMENT]),
    ("adopted", REQUIREMENT, []),
    ("adopted", REQUIREMENT.replace("DC0", "DCO"), [REQUIREMENT]),
])
def test_managed_status_identity_and_active_text_must_agree(state, identity, matching):
    stamp = artifact(status=state, selected=identity, matching=matching)
    result = apply_standard_check(receipt(stamp), standard())
    assert not result["seal_check"]["reliable"]
    assert result["seal_standard_check"]["status"] == "unmatched"


def test_selected_identity_is_the_only_comparable_managed_string():
    stamp = artifact(status="adopted", selected=REQUIREMENT, matching=[REQUIREMENT, "另一仓库章"])
    stamp.update(rectangular_text="错误旧公司章", combined_text="错误旧公司章",
                 reads=[{"text": "错误旧公司章", "channel": "body", "matching": True}])
    result = apply_standard_check(receipt(stamp), standard())
    assert result["seal_standard_check"]["candidates"][0]["texts"] == [REQUIREMENT]
    assert result["seal_check"]["status"] == "匹配" and result["seal_check"]["reliable"]
    assert result["overall"] == "通过"


def test_equivalent_spacing_of_the_selected_complete_identity_is_preserved():
    text = "上海朱泾DC 021DC0 收货专用章①"
    stamp = artifact(status="adopted", selected=REQUIREMENT, matching=[text])
    assert seal_candidates(receipt(stamp))[0]["texts"] == [text]
    assert apply_standard_check(receipt(stamp), standard())["seal_check"]["status"] == "匹配"


def test_exact_looking_split_candidate_does_not_enter_selected_identity_matching():
    selected = REQUIREMENT.replace("021", "031")
    stamp = artifact(status="adopted", selected=selected, matching=[selected])
    stamp["receiving_form"]["split"] = {"accepted": True, "identity_text": REQUIREMENT,
                                        "requires_review": True, "matching_allowed": False}
    stamp["reads"] = [{"text": REQUIREMENT, "channel": "receiving_split", "confidence": .99,
                       "matching": False}]
    candidate = seal_candidates(receipt(stamp))[0]
    assert candidate["texts"] == [selected]
    assert candidate["text_status"] == "不匹配"
    assert apply_standard_check(receipt(stamp), standard())["overall"] != "通过"


def test_ordinary_unmanaged_local_stamp_retains_its_existing_matching_path():
    stamp = artifact(managed=False)
    stamp["reads"] = [{"text": REQUIREMENT, "channel": "body", "confidence": .99}]
    result = apply_standard_check(receipt(stamp), standard())
    assert result["seal_check"]["status"] == "匹配"
    assert result["overall"] == "通过"


def test_separate_remote_stamp_retains_its_explicit_api_evidence():
    blocked, remote = artifact(0), artifact(1, managed=False)
    remote.update(source="清瞳印章区域", api_index=7)
    item = receipt(blocked, remote)
    item["seal_check"]["dual_check"] = {"candidates": [
        {"index": 7, "ocr": {"text": REQUIREMENT}}]}
    result = apply_standard_check(item, standard())
    candidates = result["seal_standard_check"]["candidates"]
    assert candidates[0]["texts"] == [] and not candidates[0]["matched"]
    assert candidates[1]["source"] == "qingtong" and candidates[1]["matched"]
    assert result["seal_check"]["status"] == "匹配"


@pytest.mark.parametrize('state,selected,matching', [
    ('conflict', '', []), ('insufficient', '', []),
    ('adopted', REQUIREMENT.replace('021', '031'), [REQUIREMENT.replace('021', '031')]),
])
def test_same_managed_stamp_cannot_bypass_selected_identity_through_remote_ocr(state, selected, matching):
    stamp = artifact(status=state, selected=selected, matching=matching)
    stamp.update(source='清瞳印章区域', api_index=7)
    item = receipt(stamp)
    item['seal_check']['dual_check'] = {'candidates': [{'index': 7, 'ocr': {'text': REQUIREMENT}}]}
    before = deepcopy(item)
    result = apply_standard_check(item, standard())
    candidate = result['seal_standard_check']['candidates'][0]
    assert candidate['texts'] == matching
    assert not candidate['matched']
    assert result['seal_check']['status'] != '匹配'
    assert result['overall'] != '通过'
    assert item == before


def test_company_from_one_managed_stamp_and_purpose_from_another_never_combine():
    company = artifact(0, status="adopted", selected="上海朱泾DC021DC0", matching=["上海朱泾DC021DC0"])
    purpose = artifact(1, status="adopted", selected="收货专用章①", matching=["收货专用章①"])
    result = apply_standard_check(receipt(company, purpose), standard())
    assert result["seal_standard_check"]["status"] == "unmatched"
    assert not result["seal_check"]["reliable"]


def test_text_and_shape_cannot_be_borrowed_across_a_blocked_and_a_valid_stamp():
    blocked = artifact(0)
    blocked["rectangular_text"] = REQUIREMENT
    valid_text = artifact(1, managed=False, shape="round")
    valid_text["reads"] = [{"text": REQUIREMENT, "channel": "body", "confidence": .99}]
    result = apply_standard_check(receipt(blocked, valid_text), standard())
    assert result["seal_standard_check"]["status"] == "unmatched"
    assert result["overall"] != "通过"


@pytest.mark.parametrize("confirmed", [True, False])
def test_explicit_human_decision_survives_machine_standard_rechecking(confirmed):
    item = receipt(artifact())
    item["seal_check"].update(human_confirmed_match=confirmed, reliable=True,
                               status="匹配" if confirmed else "不匹配", source="人工复核")
    expected = deepcopy(item["seal_check"])
    result = apply_standard_check(item, standard())
    assert result["seal_check"] == expected


@pytest.mark.parametrize("review_status,overall", [("确认通过", "通过"), ("确认不通过", "不通过")])
def test_confirmed_overall_review_is_not_overwritten(review_status, overall):
    item = receipt(artifact())
    item.update(review_status=review_status, overall=overall, final_result=overall)
    expected = deepcopy(item["seal_check"])
    result = apply_standard_check(item, standard())
    assert result["seal_check"] == expected and result["overall"] == overall
