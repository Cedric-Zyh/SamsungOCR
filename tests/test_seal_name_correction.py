from copy import deepcopy
from contextvars import copy_context

import pytest

from receipt_ocr.recognition.seal.postprocess.name_correction import (
    company_name_correction_scope, correct_company_name, correct_local_seal_names,
    registered_company_names,
)

PARENT = "深圳欧瑞特供应链管理有限公司"
NAMES = [PARENT + city + "分公司" for city in ["重庆", "成都", "南京", "呼和浩特", "长春", "长沙"]]
STANDARDS = [{"requirement": name + "收货专用章(1)", "recognized": "错误OCR不能成为词库"}
             for name in NAMES]


@pytest.mark.parametrize("raw,expected", [
    ("深圳欧瑞特供座链管理有限公司重庆分公司", PARENT + "重庆分公司"),
    ("深圳欧瑞特供应链管理限公司成都分公司", PARENT + "成都分公司"),
    ("铁" + PARENT + "南京分公司合", PARENT + "南京分公司"),
    (PARENT + "长春分公司司", PARENT + "长春分公司"),
    (PARENT + "呼租浩特分公司", PARENT + "呼和浩特分公司"),
    (PARENT + "成部分公司", PARENT + "成都分公司"),
])
def test_documented_errors_have_a_unique_registered_correction(raw, expected):
    with company_name_correction_scope(STANDARDS):
        result = correct_company_name(raw)
    assert result["raw_text"] == raw
    assert result["corrected_text"] == expected
    assert result["changes"]


def test_supply_chain_typo_uses_only_unique_registered_name_correction():
    raw = NAMES[0].replace('供应链', '供座链')
    result = correct_company_name(raw, names=NAMES)
    assert result['corrected_text'] == NAMES[0]
    assert [change['rule'] for change in result['changes']] == ['unique_registered_name']
    assert result['changes'][0]['character_edits'] == 1
    assert result['raw_text'] == raw


def test_supply_chain_typo_is_not_replaced_without_unique_registered_evidence():
    raw = NAMES[0].replace('供应链', '供座链')
    assert correct_company_name(raw, names=[]) is None
    assert correct_company_name(raw, names=[raw]) is None
    alternative = NAMES[0].replace('供应链', '供备链')
    assert correct_company_name(raw, names=[NAMES[0], alternative]) is None


def test_supply_chain_typo_counts_toward_the_total_name_edit_limit():
    raw = NAMES[0].replace('供应链', '供座链').replace('管理', '管埋').replace('瑞', '端')
    assert correct_company_name(raw, names=[NAMES[0]]) is None


@pytest.mark.parametrize("raw", [
    *NAMES, PARENT + "长分公司", "北京其他电子科技有限公司", "(1)",
    "三星售后6183342站", "电话:020-31103112", "收货专用章(2)",
    "铁铁铁" + PARENT + "南京分公司合合合",
    "铁铁铁" + PARENT + "南京分公司",
    PARENT + "南京分公司合合合",
])
def test_exact_ambiguous_unrelated_and_number_readings_are_not_rewritten(raw):
    with company_name_correction_scope(STANDARDS):
        assert correct_company_name(raw) is None


def test_valid_other_branch_is_not_replaced_by_requested_branch():
    # No per-document requested wording is accepted by the correction API.
    with company_name_correction_scope(STANDARDS):
        assert correct_company_name(PARENT + "重庆分公司") is None
    with company_name_correction_scope([STANDARDS[1]]):  # only Chengdu registered
        assert correct_company_name(PARENT + "重庆分公司") is None


def test_company_digits_are_not_fuzzy_corrected():
    with company_name_correction_scope([{"requirement": "甲乙电子第12服务中心收货专用章"}]):
        assert correct_company_name("甲乙电子第13服务中心") is None


def test_vocabulary_comes_from_registered_requirements_and_scope_isolated():
    names = registered_company_names(STANDARDS)
    assert PARENT in names and PARENT + "南京分公司" in names
    assert all("收货专用章" not in name and "错误OCR" not in name for name in names)
    raw = PARENT + "呼租浩特分公司"
    assert correct_company_name(raw) is None
    with company_name_correction_scope(STANDARDS):
        result = copy_context().run(correct_company_name, raw)
        assert result["corrected_text"] == PARENT + "呼和浩特分公司"
    assert correct_company_name(raw) is None


def test_pipeline_artifact_retains_raw_reads_number_and_provenance():
    raw = "铁" + PARENT + "南京分公司合"
    identity = raw + "收货专用章(2)"
    artifact = {"shape_code": "round", "unwrapped_text": raw, "unwrapped_raw_text": raw,
                "combined_text": identity, "matching_texts": [identity],
                "stamp_number_text": "(2)", "reads": [{"text": raw, "channel": "ring"},
                                                          {"text": "(2)", "channel": "number"}]}
    original = deepcopy(artifact)
    with company_name_correction_scope(STANDARDS):
        texts = correct_local_seal_names(artifact, [identity, "(2)"])
    assert texts == [PARENT + "南京分公司收货专用章(2)", "(2)"]
    assert artifact["unwrapped_raw_text"] == original["unwrapped_raw_text"]
    assert artifact["reads"][0]["text"] == raw
    assert artifact["reads"][1] == original["reads"][1]
    assert artifact["stamp_number_text"] == "(2)"
    assert artifact["name_correction_raw_fields"]["combined_text"] == identity
    assert artifact["name_corrections"][0]["corrected_text"] == PARENT + "南京分公司"


def test_other_company_rectangular_name_uses_same_dictionary():
    name = "北京甲乙电子科技有限公司"
    raw = "海" + name + "合"
    artifact = {"shape_code": "rectangle", "rectangular_row_texts": [raw, "业务受理", "(3)"],
                "rectangular_text": raw + "业务受理(3)", "reads": []}
    with company_name_correction_scope([{"requirement": name + "业务受理"}]):
        texts = correct_local_seal_names(artifact, [artifact["rectangular_text"]])
    assert artifact["rectangular_row_texts"] == [name, "业务受理", "(3)"]
    assert texts == [name + "业务受理(3)"]


def test_live_pipeline_uses_library_scope_and_preserves_the_actual_other_branch(tmp_path, monkeypatch):
    from receipt_ocr.domain.ocr import TextObservation
    from receipt_ocr.recognition.seal import pipeline

    raw = "铁" + PARENT + "南京分公司合"
    artifact = {"index": 0, "region_id": "seal-0", "shape_code": "round",
                "reads": [], "inputs": [], "errors": []}
    image_input = {"id": "seal-0:ring", "channel": "ring", "image_url": "/ring.png"}
    monkeypatch.setattr(pipeline, "prepare_inputs", lambda *a, **k:
                        (artifact, [(image_input, tmp_path / "ring.png")]))

    class Reader:
        def read_line(self, *a, **k):
            return [TextObservation(raw, .99, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    monkeypatch.setattr(pipeline, "read_double_border", lambda *a, **k: False)
    monkeypatch.setattr(pipeline, "read_continuous_company", lambda *a, **k: False)
    with company_name_correction_scope(STANDARDS):
        texts, artifacts = pipeline._recognize_local_seals(
            tmp_path / "receipt.png", [], [object()], tmp_path, "/test", "paddle_v6",
            requirement=PARENT + "成都分公司收货专用章(1)")
    assert artifacts[0]["unwrapped_text"] == PARENT + "南京分公司"
    assert artifacts[0]["unwrapped_raw_text"] == raw
    assert artifacts[0]["reads"][0]["text"] == raw
    assert PARENT + "南京分公司" in texts
    assert PARENT + "成都分公司" not in texts
