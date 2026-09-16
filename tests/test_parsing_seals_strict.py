import pytest

from receipt_ocr.parsing_seals import compare_seal_text_strict


@pytest.mark.parametrize('expected,actual', [
    ('太原市伊加壹电子服务总汇', '太原市伊服壹电子服务总汇'),
    ('客户收货专用章', '客户收货专用章章'),
    ('客户收货专用章', '客户货收专用章'),
    ('客户1收货章', '客户①收货章'),
    ('客户A收货章', '客户a收货章'),
])
def test_wrong_extra_or_reordered_content_is_a_reliable_mismatch(expected, actual):
    result = compare_seal_text_strict(expected, [actual])
    assert result['status'] == '不匹配'
    assert result['reliable'] is True
    assert result['recognized'] == actual


@pytest.mark.parametrize('expected,actual', [
    ('客户（1）收货章', '客户(1)收货章'),
    ('客户（1）收货章', '客户1收货章'),
    ('客户1收货章', '（客户）(1收货章'),
    ('客户（Ａ１）收货章', '客户(A1)收货章'),
    ('客户收货章', '客户 收\n货\t章'),
])
def test_width_and_spacing_differences_are_ignored(expected, actual):
    result = compare_seal_text_strict(expected, [actual])
    assert (result['status'], result['reliable'], result['score']) == ('匹配', True, 1.0)


@pytest.mark.parametrize('expected,texts,status', [
    ('', ['客户收货章'], '无法判断'),
    ('客户收货章', [], '未识别'),
    ('客户收货章', [' \n\t'], '未识别'),
    ('客户收货章', ['（()）'], '未识别'),
    ('（()）', ['客户收货章'], '无法判断'),
])
def test_missing_evidence_does_not_become_a_reliable_comparison(expected, texts, status):
    result = compare_seal_text_strict(expected, texts)
    assert result['status'] == status
    assert result['reliable'] is False


def test_exact_candidate_wins_without_modifying_evidence():
    texts = ['太原市伊服壹电子服务总汇', '太原市伊加壹电子服务总汇']
    result = compare_seal_text_strict(texts[1], texts)
    assert result['status'] == '匹配'
    assert result['recognized'] == texts[1]
    assert result['all_recognized'] == texts


@pytest.mark.parametrize('expected,actual', [
    ('客户收货专用章', '客户收货专用'),
    ('客户收货专用章', '收货专用章'),
    ('客户收货专用章', '客户专用章'),
    ('客户收货专用章', '客收专章'),
    ('客户收货专用章', '章'),
])
def test_only_missing_characters_is_partial_even_for_internal_omissions(expected, actual):
    result = compare_seal_text_strict(expected, [actual])
    assert result['status'] == '部分匹配'
    assert result['reliable'] is False


def test_partial_candidate_wins_over_higher_similarity_wrong_text():
    result = compare_seal_text_strict('太原市伊加壹电子服务总汇', ['太原市伊服壹电子服务总汇', '电子服务'])
    assert result['status'] == '部分匹配'
    assert result['recognized'] == '电子服务'


def test_required_character_coverage_ranks_incorrect_text_before_sequence_similarity():
    requirement = '合肥佳元电子第一分公司手机售后专用章'
    short_wrong = '合肥佳元电子第分公司上'
    full_with_extra = '合肥佳元电子第一分公司手机售后专用章2025年02月21日签收日期'

    result = compare_seal_text_strict(requirement, [short_wrong, full_with_extra])
    short = compare_seal_text_strict(requirement, [short_wrong])

    assert result['status'] == '不匹配'
    assert result['recognized'] == full_with_extra
    assert result['requirement_coverage'] == 1.0
    assert short['requirement_coverage'] == pytest.approx(10 / 18, abs=1e-6)


def test_required_character_coverage_uses_ordered_matching_and_repeated_counts():
    result = compare_seal_text_strict('甲乙甲乙丙', ['甲甲乙'])
    assert result['status'] == '部分匹配'
    assert result['requirement_coverage'] == .6
