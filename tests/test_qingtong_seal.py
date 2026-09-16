from copy import deepcopy

import pytest

from receipt_ocr.qingtong_seal import compare_qingtong_seal
from receipt_ocr.decision import decide_overall
from receipt_ocr.seal_reference_policy import apply_reference_evidence

REQUIRED = '北京恒远恒信科技发展有限公司收发货专用章'
OTHER = '广州市星睿奇光电有限公司仓储部收货章'


def response(*seals):
    return {'enabled': True, 'ok': True, 'response': {'data': {'img_0': list(seals)}}}


def seal(label=REQUIRED, text=REQUIRED, similarity=.01, text_similarity=.01):
    return {'matched_seal': {'label': label, 'similarity': similarity}, 'text_formatted': text,
            'text_similarity': text_similarity, 'xyxy': [1, 2, 30, 40]}


@pytest.mark.parametrize('label,text,status,reliable', [
    (REQUIRED, REQUIRED, '匹配', True), (OTHER, REQUIRED, '匹配', True),
    (REQUIRED, OTHER, '匹配', True), (OTHER, OTHER, '不匹配', True),
    ('', REQUIRED, '匹配', True), (REQUIRED, '', '匹配', True),
    (OTHER, '北京恒远恒信科技发展有限公司', '部分匹配', False),
    ('北京恒远恒信科技发展有限公司', OTHER, '部分匹配', False),
])
def test_independent_channels_determine_status(label, text, status, reliable):
        check = compare_qingtong_seal(REQUIRED, response(seal(label, text)))
        assert (check['status'], check['reliable']) == (status, reliable)
        assert decide_overall({'status': '匹配', 'reliable': True}, check, []) == {
            '匹配': '通过', '部分匹配': '需人工复核', '不匹配': '需人工复核'}[status]


def test_all_channel_mode_requires_both_qingtong_results_to_match():
    check = compare_qingtong_seal(REQUIRED, response(seal(REQUIRED, OTHER)), match_mode='all')
    assert check['status'] == '部分匹配'
    assert check['reliable'] is False
    assert check['dual_check']['policy'] == 'qingtong_all_channel'


def test_api_similarity_scores_do_not_vote_or_gate_a_match():
    for score in (None, .0, .37, .926, 1.0, 'invalid'):
        assert compare_qingtong_seal(REQUIRED, response(seal(similarity=score, text_similarity=score)))['status'] == '匹配'
        assert compare_qingtong_seal(REQUIRED, response(seal(OTHER, OTHER, score, score)))['status'] == '不匹配'


def test_one_different_ocr_character_is_a_mismatch_even_with_high_similarity():
    requirement = '太原市伊加壹电子服务总汇'
    recognized = '太原市伊服壹电子服务总汇'

    check = compare_qingtong_seal(requirement, response(seal(OTHER, recognized, .84, .99)))

    assert (check['status'], check['reliable']) == ('不匹配', True)
    ocr = check['dual_check']['selected']['ocr']
    assert not ocr['matched']
    assert ocr['comparison']['status'] == '不匹配'
    assert ocr['comparison']['score'] > .9
    assert check['recognized'] == recognized


@pytest.mark.parametrize('recognized,status', [
    ('太原市伊服壹电子服务总汇', '不匹配'),
    ('太原市伊加壹电子服务总汇章', '不匹配'),
    ('太原市伊加壹电子服务总', '部分匹配'),
])
def test_template_has_independent_status_even_when_ocr_completely_matches(recognized, status):
    requirement = '太原市伊加壹电子服务总汇'
    check = compare_qingtong_seal(requirement, response(seal(recognized, requirement)))

    assert (check['status'], check['reliable']) == ('匹配', True)
    assert not check['dual_check']['selected']['template']['matched']
    assert check['dual_check']['selected']['template']['requirement_match']['status'] == status


def test_chinese_and_english_parentheses_do_not_cause_a_mismatch():
    requirement = '北京恒远恒信科技发展有限公司（收发货专用章）'
    check = compare_qingtong_seal(requirement, response(seal(
        '北京恒远恒信科技发展有限公司(收发货专用章)',
        '北京恒远恒信科技发展有限公司 （收发货\n专用章）',
    )))

    assert (check['status'], check['reliable']) == ('匹配', True)
    assert check['dual_check']['selected']['match_count'] == 2


def test_matching_template_supplies_display_text_and_source_instead_of_wrong_ocr():
    check = compare_qingtong_seal(REQUIRED, response(seal(REQUIRED, OTHER)))

    assert check['recognized'] == REQUIRED
    assert check['source'] == '清瞳 · 印章模板识别'
    assert check['requirement_coverage'] == 1.0
    assert check['dual_check']['selected']['ocr']['text'] == OTHER
    assert check['dual_check']['selected']['ocr']['status'] == '不匹配'
    assert check['all_recognized'] == [OTHER, REQUIRED]


def test_partial_candidate_is_selected_before_higher_similarity_wrong_candidate():
    requirement = '太原市伊加壹电子服务总汇'
    check = compare_qingtong_seal(requirement, response(
        seal(OTHER, '太原市伊服壹电子服务总汇'),
        seal('电子服务', OTHER),
    ))

    assert (check['status'], check['reliable']) == ('部分匹配', False)
    assert check['dual_check']['selected']['index'] == 1
    assert check['recognized'] == '电子服务'
    assert check['source'] == '清瞳 · 印章模板识别'


def test_required_character_coverage_selects_best_wrong_candidate_and_channel():
    requirement = '合肥佳元电子第一分公司手机售后专用章'
    short_wrong = '合肥佳元电子第分公司上'
    full_with_extra = requirement + '2025年02月21日签收日期'
    check = compare_qingtong_seal(requirement, response(
        seal(OTHER, short_wrong),
        seal(full_with_extra, OTHER),
    ))

    assert check['status'] == '不匹配'
    assert check['dual_check']['selected']['index'] == 1
    assert check['recognized'] == full_with_extra
    assert check['requirement_coverage'] == 1.0


def test_any_single_complete_channel_matches_without_flattening_raw_text():
    first, second = seal(REQUIRED, OTHER), seal(OTHER, REQUIRED)
    first['raw_texts'] = [REQUIRED]
    check = compare_qingtong_seal(REQUIRED, response(first, second))
    assert check['status'] == '匹配'
    assert len(check['dual_check']['candidates']) == 2
    assert compare_qingtong_seal(REQUIRED, response(first, second, seal()))['status'] == '匹配'
    partial_one, partial_two = seal('北京恒远恒信', OTHER), seal(OTHER, '科技发展有限公司收发货专用章')
    partial_one['raw_texts'] = [REQUIRED]
    assert compare_qingtong_seal(REQUIRED, response(partial_one, partial_two))['status'] == '部分匹配'


@pytest.mark.parametrize('data', [seal('', ''), seal(OTHER, ''), seal('', OTHER), seal(OTHER, '（()）')])
def test_missing_channel_does_not_become_reliable_rejection(data):
    check = compare_qingtong_seal(REQUIRED, response(data))
    assert not check['reliable']
    assert check['status'] == '不匹配'


def test_missing_requirements_and_failed_api_cannot_pass():
    assert compare_qingtong_seal('', response(seal()))['status'] == '缺少比对依据'
    assert compare_qingtong_seal(REQUIRED, response())['status'] == '未识别'
    external = response(seal()); external['ok'] = False
    assert compare_qingtong_seal(REQUIRED, external)['status'] == '识别失败'


@pytest.mark.parametrize('label,text', [
    ('中国外运物流发展有限公司广州分公司商品付讫章', REQUIRED),
    (REQUIRED, '中国外运物流发展有限公司广州分公司商品付讫章'),
    ('中国外运物流发展有限公司广州分公司商品付讫章',
     '中国外运物流发展有限公司广州分公司商品付讫章'),
    (REQUIRED, '中国外运物流发展有限公司广州分公司商品 付\n讫 章'),
])
def test_dispatch_stamp_is_excluded_before_selecting_customer_seal(label, text):
    dispatch, customer = seal(label, text), seal()
    customer['xyxy'] = [100, 200, 300, 400]
    external = response(dispatch, customer)
    original = deepcopy(external)

    check = compare_qingtong_seal(REQUIRED, external)

    assert check['status'] == '匹配'
    assert check['recognized'] == REQUIRED
    assert check['all_recognized'] == [REQUIRED]
    assert [c['index'] for c in check['dual_check']['candidates']] == [1]
    assert check['dual_check']['selected']['index'] == 1
    assert check['dual_check']['selected']['xyxy'] == customer['xyxy']
    assert check['dual_check']['excluded_candidates'][0]['index'] == 0
    assert external == original


@pytest.mark.parametrize('requirement', [REQUIRED, '', '中国外运物流发展有限公司广州分公司商品付讫章'])
def test_dispatch_stamp_alone_never_becomes_customer_seal(requirement):
    dispatch = '中国外运物流发展有限公司广州分公司商品付讫章'
    check = compare_qingtong_seal(requirement, response(seal(dispatch, dispatch)))

    assert check['dual_check']['selected'] is None
    assert check['dual_check']['candidates'] == []
    assert not check['recognized']
    assert not check['reliable']
    assert check['status'] == ('未识别' if requirement else '缺少比对依据')
    if requirement:
        assert '已排除商品付讫章' in check['message']


def test_dispatch_exclusion_does_not_remove_goods_received_stamp():
    received = '中国外运物流发展有限公司广州分公司商品收讫章'
    check = compare_qingtong_seal(received, response(seal(received, received)))

    assert check['status'] == '匹配'
    assert check['recognized'] == received
    assert check['dual_check']['excluded_candidates'] == []


def test_reference_matcher_does_not_override_qingtong_channel_match():
    class Matcher:
        def match(self, result):
            raise AssertionError('QingTong must use its own two channels')
    result = {'seal_check': compare_qingtong_seal(REQUIRED, response(seal(OTHER, REQUIRED)))}
    assert apply_reference_evidence(result, Matcher())['seal_check']['status'] == '匹配'


def test_pending_save_preserves_ocr_channel_complete_match():
    from app import _apply_human_edits
    check = compare_qingtong_seal(REQUIRED, response(seal(OTHER, REQUIRED)))
    record = {'fields': {'签章要求': REQUIRED, '要求到货': '2026-09-10'},
              'seal_check': check, 'date_check': {'actual': '2026-09-10'}}
    updated = _apply_human_edits(deepcopy(record), {'review_status': '待复核', 'actual_date': '2026-09-10'})
    assert updated['seal_check']['status'] == '匹配'
    assert updated['seal_check']['reliable']
    assert updated['seal_check']['dual_check'] == check['dual_check']


@pytest.mark.parametrize('matches', [True, False])
def test_explicit_seal_confirmation_persists_on_pending_save_with_machine_evidence(matches):
    from app import _apply_human_edits, _confirmation_error
    check = compare_qingtong_seal(REQUIRED, response(seal(OTHER, REQUIRED)))
    record = {'fields': {'签章要求': REQUIRED, '要求到货': '2026-09-10'},
              'seal_check': check, 'date_check': {'actual': '2026-09-10'}}
    updated = _apply_human_edits(deepcopy(record), {'review_status': '待复核',
                                 'actual_date': '2026-09-10', 'actual_date_confirmed': True,
                                 'seal_confirmed_match': matches})
    assert updated['seal_check']['status'] == ('匹配' if matches else '不匹配')
    assert updated['seal_check']['human_confirmed_match'] is matches
    assert updated['seal_check']['reliable']
    assert updated['seal_check']['dual_check'] == check['dual_check']
    assert bool(_confirmation_error(updated, '确认通过', '通过')) is not matches


def test_editing_confirmed_seal_invalidates_previous_manual_choice():
    from app import _apply_human_edits
    check = compare_qingtong_seal(REQUIRED, response(seal(OTHER, REQUIRED)))
    record = {'fields': {'签章要求': REQUIRED, '要求到货': '2026-09-10'}, 'seal_check': check}
    confirmed = _apply_human_edits(deepcopy(record), {'review_status': '待复核', 'seal_confirmed_match': True})
    edited = _apply_human_edits(confirmed, {'review_status': '待复核', 'seal_text': OTHER, 'seal_confirmed_match': None})
    assert edited['seal_check']['status'] == '需人工复核'
    assert not edited['seal_check']['reliable']
    assert 'human_confirmed_match' not in edited['seal_check']
    assert edited['seal_check']['dual_check'] == check['dual_check']
