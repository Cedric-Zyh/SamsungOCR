from copy import deepcopy

from receipt_ocr.parsing_seals import compare_seal_text_strict
from receipt_ocr.qingtong_seal import compare_qingtong_seal
from receipt_ocr.seal_provider_policy import combine_seal_provider_checks


def dzt(required, text, simulated=False):
    check = compare_seal_text_strict(required, [text])
    check.update(backend='单证通', source='单证通', recognition_mode='danzhengtong', simulated=simulated)
    if simulated:
        check['reliable'] = False
    return check


def qt(required, template, ocr):
    return compare_qingtong_seal(required, {'ok': True, 'response': {'data': {'img_0': [{
        'matched_seal': {'label': template}, 'text_formatted': ocr, 'xyxy': [1, 2, 30, 40]}]}}})


def test_best_main_text_prioritizes_full_requirement_coverage_within_mismatches():
    required = '合肥佳元电子第一分公司手机售后专用章'
    text = '合肥佳元电子通讯产品技术服务有限公司第一分公司 手机售后专用章 2025年02月11日'
    checks = [qt(required, '深圳欧瑞特供应链管理有限公司石家庄分公司收货专用章', '合肥佳元电子第分公司上'),
              dzt(required, text)]
    original = deepcopy(checks)
    result = combine_seal_provider_checks(checks)
    assert result['status'] == '不匹配'
    assert result['recognized'] == text
    assert result['source'] == '单证通'
    assert result['dual_check'] == original[0]['dual_check']
    assert result['api'] == original[0]['api']
    assert checks == original


def test_partial_reading_beats_more_similar_wrong_character_reading():
    required = '太原市伊加壹电子服务总汇'
    result = combine_seal_provider_checks([qt(required, '无关客户章', '太原市伊服壹电子服务总汇'),
                                           dzt(required, '伊加壹电子服务')])
    assert result['status'] == '部分匹配'
    assert result['recognized'] == '伊加壹电子服务'
    assert result['reliable'] is False


def test_simulated_exact_reading_cannot_replace_real_mismatch():
    required = '客户收货专用章'
    result = combine_seal_provider_checks([qt(required, '错误客户章', '错误客户章'),
                                           dzt(required, required, simulated=True)])
    assert result['status'] == '不匹配'
    assert result['recognized'] == '错误客户章'
    assert result['reliable'] is False


def test_local_fuzzy_evidence_does_not_add_a_fourth_vote():
    assert combine_seal_provider_checks([{'backend': 'Paddle', 'recognized': '客户章', 'status': '匹配', 'reliable': True}]) is None
