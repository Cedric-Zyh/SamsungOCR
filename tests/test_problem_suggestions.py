from copy import deepcopy

import pytest

from receipt_ocr.domain.problem_suggestions import suggest_problems
from receipt_ocr.domain.sample_problems import PROBLEM_TYPES


def labels(entry, result):
    suggestions = suggest_problems(entry, result)
    assert len({row['type'] for row in suggestions}) == len(suggestions)
    assert all(row['type'] in PROBLEM_TYPES and row['reason'] for row in suggestions)
    return {row['type']: row['reason'] for row in suggestions}


def test_saved_date_and_business_mismatch_are_independent_and_never_change_inputs():
    entry = {'actual_date': '2026-03-22', 'fields': {'要求到货': '2026-03-23'}}
    result = {'date_check': {'actual': '2026-03-02', 'status': '不匹配', 'reliable': True}}
    before = deepcopy((entry, result))
    problems = labels(entry, result)
    assert set(problems) == {'日期读错', '原单日期不符'}
    assert '2026-03-02' in problems['日期读错'] and '2026-03-22' in problems['日期读错']
    assert (entry, result) == before


def test_true_business_mismatch_with_correct_reading_is_not_an_ocr_error():
    entry = {'actual_date': '2026-03-22', 'fields': {'要求到货': '2026-03-23'}, 'seal_should_match': False}
    result = {'date_check': {'actual': '2026/3/22', 'status': '不匹配', 'reliable': True},
              'seal_check': {'status': '不匹配', 'recognized': '客户章', 'reliable': True}}
    assert set(labels(entry, result)) == {'原单日期不符'}


@pytest.mark.parametrize('entry', [{'date_present': False}, {'actual_date': ''}])
def test_verified_blank_date_read_as_value_is_a_spurious_read(entry):
    assert set(labels(entry, {'date_check': {'actual': '2026-03-27', 'status': '匹配'}})) == {'空白误识别日期'}


def test_saved_date_missing_after_execution_is_a_missed_read():
    assert '日期漏识别' in labels({'actual_date': '2026-03-27'},
                              {'date_check': {'actual': '', 'status': '未识别'}})


@pytest.mark.parametrize('result', [
    {},
    {'recognition_config': {'date': ['paddle_v6']}},
    {'recognition_config': {'date': []}, 'date_check': {'actual': '', 'status': '未识别'}},
    {'date_check': {'actual': '', 'status': '未执行'}},
    {'date_check': {'actual': '', 'status': '未识别', 'execution_status': 'skipped'}},
    {'execution_status': 'running', 'date_check': {'actual': '', 'status': '未识别'}},
    {'date_check': {'actual': '', 'status': '未核对', 'source': '按设置跳过'}},
])
def test_unexecuted_date_never_suggests_a_missed_read(result):
    assert labels({'actual_date': '2026-03-27'}, result) == {}


def test_machine_required_date_or_prefill_is_not_a_saved_truth():
    result = {'fields': {'要求到货': '2026-03-27', '客户名称': '机器客户'},
              'date_check': {'actual': '2026-03-02', 'required': '2026-03-27', 'status': '不匹配'}}
    assert labels({}, result) == {}
    assert labels({'fields': {}}, result) == {}


@pytest.mark.parametrize('truth', ['2026-02-30', None, '尚未标注'])
def test_invalid_saved_date_does_not_create_a_date_error(truth):
    assert labels({'actual_date': truth}, {'date_check': {'actual': '2026-02-28'}}) == {}


def test_multiple_seal_channels_compare_each_decision_with_truth_and_deduplicate():
    result = {'seal_check': {'status': '匹配', 'recognized': '客户章'},
              'recognition_variants': {'seal': [
                  {'method': 'qingtong', 'details': {'seal_check': {'status': '匹配', 'dual_check': {'selected': {
                      'template': {'label': '客户章', 'requirement_match': {'status': '匹配'}},
                      'ocr': {'text': '另一客户章', 'comparison': {'status': '不匹配'}},
                  }}}}},
                  {'method': 'paddle_v6', 'details': {'seal_check': {
                      'status': '不匹配', 'recognized': '另一客户章', 'reliable': False}}},
              ]}}
    problems = labels({'seal_should_match': True}, result)
    assert set(problems) == {'清瞳印章判定错误', '本地印章判定错误', '本地印章证据不足'}
    assert '文字 OCR' in problems['清瞳印章判定错误']
    assert '本地印章' in problems['本地印章判定错误']


def test_negative_seal_truth_only_flags_false_positive_decisions():
    assert set(labels({'seal_should_match': False}, {'seal_check': {
        'status': '匹配', 'recognized': '章'}})) == {'印章判定错误'}
    assert labels({'seal_should_match': False}, {'seal_check': {
        'status': '不匹配', 'recognized': '章', 'reliable': True}}) == {}
    assert labels({}, {'seal_check': {'status': '不匹配', 'recognized': '章'}}) == {}


def test_a_seal_requirement_is_not_a_transcription_truth():
    assert labels({'fields': {'签章要求': '客户甲公司专用章'}, 'seal_should_match': True}, {
        'seal_check': {'status': '匹配', 'recognized': '客户甲公司', 'reliable': True}}) == {}


def test_negative_seal_truth_does_not_prove_that_a_stamp_was_missed():
    result = {'seal_check': {'status': '未检出', 'recognized': '', 'reliable': True}}
    assert labels({'seal_should_match': False}, result) == {}
    assert '印章未检出' in labels({'seal_should_match': True}, result)


@pytest.mark.parametrize('check', [
    {'status': '未检出', 'recognized': ''},
    {'status': '未识别', 'recognized': ''},
    {'status': '无法判断', 'detected': False},
])
def test_explicit_executed_seal_missing_evidence_can_be_suggested(check):
    assert '印章未检出' in labels({'seal_should_match': True}, {'seal_check': check})


@pytest.mark.parametrize('result', [
    {},
    {'recognition_config': {'seal': []}, 'seal_check': {'status': '未检出'}},
    {'seal_check': {'status': '已跳过'}},
    {'seal_check': {'status': '未检出', 'execution_status': 'skipped'}},
    {'recognition_config': {'seal': ['paddle_v6']}, 'recognition_variants': {'seal': [
        {'method': 'qingtong', 'details': {'seal_check': {'status': '未检出'}}}]}},
])
def test_unexecuted_seal_is_not_a_detection_failure(result):
    assert labels({'seal_should_match': True}, result) == {}


def test_only_nonempty_saved_printed_fields_with_outputs_are_compared():
    entry = {'fields': {'客户名称': '人工客户', '签章要求': '', '要求到货': '2026-03-27',
                        '仓库接收人': '人工签名', '签收日期': '2026-03-27', '未知字段': '值'}}
    result = {'fields': {'客户名称': '机器客户', '签章要求': '机器要求', '仓库接收人': '机器签名',
                         '签收日期': '2026-03-02', '未知字段': '另一值'}}
    problems = labels(entry, result)
    assert set(problems) == {'基础字段错误'}
    assert problems['基础字段错误'].endswith('客户名称')
    assert labels(entry, {'recognition_config': {'fields': []}, **result}) == {}
    assert labels(entry, {'execution_status': 'skipped', **result}) == {}


def test_formatting_only_differences_and_pending_provider_fields_do_not_create_errors():
    entry = {'fields': {'客户名称': '客户 甲', '要求到货': '2026/3/27', '实收数量': '3', '拒收数量': '1'}}
    result = {'fields': {'客户名称': '客户甲', '要求到货': '2026-03-27', '实收数量': '', '拒收数量': ''}}
    assert labels(entry, result) == {}
    result['fields']['实收数量'] = '2'
    assert '基础字段错误' in labels(entry, result)


@pytest.mark.parametrize('metadata', [
    {'execution_status': 'skipped'}, {'status': '未执行'}, {'source': '待单证通提供'},
])
def test_field_metadata_can_explicitly_mark_a_value_unavailable(metadata):
    assert labels({'fields': {'客户名称': '人工客户'}}, {
        'fields': {'客户名称': '机器客户'}, 'field_metadata': {'客户名称': metadata}}) == {}


def test_explicit_failure_and_executed_unreliable_evidence_are_explained():
    problems = labels({}, {'error_message': '服务请求超时', 'date_check': {'reliable': False, 'status': '未识别'}})
    assert set(problems) == {'识别执行失败', '证据不足待核对'}
    assert '超时' in problems['识别执行失败']
    failed_date = labels({'actual_date': '2026-03-27'}, {'date_check': {
        'actual': '', 'status': '识别失败', 'message': '日期服务超时'}})
    assert set(failed_date) == {'识别执行失败'}
    assert set(labels({}, {'overall': '识别失败'})) == {'识别执行失败'}
    assert set(labels({'fields': {'客户名称': '人工客户'}}, {
        'overall': '识别失败', 'fields': {'客户名称': ''}})) == {'识别执行失败'}


def test_failure_of_a_selected_date_provider_is_reported_without_inventing_a_missed_read():
    result = {'recognition_config': {'date': ['paddle_v6']},
              'recognition_variants': {'date': [{'method': 'paddle_v6', 'error': '接口超时'}]}}
    assert set(labels({'actual_date': '2026-03-27'}, result)) == {'识别执行失败'}
    result['recognition_config']['date'] = []
    assert labels({'actual_date': '2026-03-27'}, result) == {}


def test_provider_conflict_requires_executed_evidence_and_does_not_guess_image_features():
    issue = {'code': 'provider_conflict', 'scope': 'date', 'message': '两个日期通道结论不同'}
    assert labels({}, {'review_issues': [issue]}) == {}
    problems = labels({}, {'date_check': {'actual': '2026-03-27'}, 'review_issues': [issue]})
    assert problems == {'证据不足待核对': issue['message']}


@pytest.mark.parametrize('entry,result', [(None, None), ([], []), ({}, {'recognition_variants': []}),
                                         ({'fields': []}, {'fields': [], 'seal_check': []})])
def test_malformed_optional_historical_data_does_not_crash(entry, result):
    assert labels(entry, result) == {}


def seal_variant(method, check=None, **extra):
    return {'method': method, 'details': {'seal_check': check or {}}, **extra}


def dual_result(template='不匹配', ocr='不匹配'):
    return {'status': '不匹配', 'recognized': '清瞳读数', 'reliable': True, 'dual_check': {'selected': {
        'template': {'label': '模板读数', 'requirement_match': {'status': template, 'reliable': True}},
        'ocr': {'text': 'OCR读数', 'comparison': {'status': ocr, 'reliable': True}},
    }}}


def test_merged_match_only_attributes_bad_independent_qingtong_readings_and_deduplicates():
    local = {'status': '匹配', 'recognized': '本地正确章', 'reliable': True,
             'backend': 'PaddleOCR PP-OCRv6 Small', 'recognition_mode': 'local'}
    qt = dual_result()
    result = {'recognition_config': {'seal': ['paddle_v6', 'qingtong']},
              'seal_check': {**qt, **local, 'local_channel': deepcopy(local)},
              'recognition_variants': {'seal': [seal_variant('paddle_v6', deepcopy(local)),
                                               seal_variant('qingtong', deepcopy(qt))]}}
    before = deepcopy(result)
    suggestions = labels({'seal_should_match': True}, result)
    assert set(suggestions) == {'清瞳印章判定错误'}
    reason = suggestions['清瞳印章判定错误']
    assert reason.count('模板判定不匹配') == 1
    assert reason.count('文字 OCR判定不匹配') == 1
    assert '印章主结果' not in reason and '本地' not in reason
    assert result == before


def test_explicit_qingtong_channel_is_used_instead_of_merged_parent_verdict():
    qt = dual_result('匹配', '匹配')
    local = {'status': '不匹配', 'recognized': '本地错误章', 'reliable': True}
    result = {'seal_check': {**qt, 'status': '不匹配', 'recognized': '融合后另一个值',
                             'qingtong_channel': qt, 'local_channel': local}}
    assert set(labels({'seal_should_match': True}, result)) == {'本地印章判定错误'}


def test_both_stamp_sources_can_have_independent_problems():
    result = {'recognition_variants': {'seal': [
        seal_variant('qingtong', {'status': '不匹配', 'recognized': '清瞳错章', 'reliable': True}),
        seal_variant('paddle_seal', {'status': '不匹配', 'recognized': '本地错章', 'reliable': True}),
    ]}}
    assert set(labels({'seal_should_match': True}, result)) == {'清瞳印章判定错误', '本地印章判定错误'}


def test_local_ocr_in_a_qingtong_region_remains_local():
    local = {'status': '不匹配', 'recognized': '错章', 'reliable': False,
             'comparison_policy': 'strict_text', 'region_source': '清瞳印章区域',
             'source': '清瞳印章区域'}
    result = {'seal_check': local, 'recognition_config': {'seal': ['paddle_v6']}}
    assert set(labels({'seal_should_match': True}, result)) == {'本地印章判定错误', '本地印章证据不足'}


@pytest.mark.parametrize('method,prefix', [('qingtong', '清瞳印章'), ('paddle_v6', '本地印章'),
                                         ('paddle_seal', '本地印章'), ('paddle_server', '本地印章')])
def test_known_sources_label_detection_and_reliability(method, prefix):
    result = {'recognition_variants': {'seal': [seal_variant(method, {
        'status': '未检出', 'recognized': '', 'reliable': False})]}}
    assert set(labels({'seal_should_match': True}, result)) == {prefix + '未检出', prefix + '证据不足'}


@pytest.mark.parametrize('method,prefix', [('qingtong', '清瞳印章'), ('paddle_v6', '本地印章')])
def test_known_source_failures_do_not_become_generic_or_another_source(method, prefix):
    result = {'recognition_config': {'seal': [method]},
              'recognition_variants': {'seal': [seal_variant(method, error='请求超时')]},
              'review_issues': [{'code': 'provider_failure', 'scope': 'seal', 'message': '该通道失败'}]}
    assert set(labels({}, result)) == {prefix + '识别失败'}
    result['recognition_config']['seal'] = []
    assert labels({}, result) == {}


@pytest.mark.parametrize('method', ['danzhengtong', 'another_provider'])
def test_other_or_unknown_sources_are_not_assigned_to_local(method):
    result = {'recognition_variants': {'seal': [seal_variant(method, {
        'status': '不匹配', 'recognized': '其他来源的章', 'reliable': False})]}}
    assert set(labels({'seal_should_match': True}, result)) == {'印章判定错误', '证据不足待核对'}


def test_unknown_fused_main_result_does_not_add_generic_error_beside_known_variants():
    result = {'seal_check': {'status': '不匹配', 'recognized': '融合值'},
              'recognition_variants': {'seal': [seal_variant('qingtong', {
                  'status': '不匹配', 'recognized': '清瞳错章'})]}}
    assert set(labels({'seal_should_match': True}, result)) == {'清瞳印章判定错误'}


def test_disabled_remote_evidence_does_not_leak_from_an_old_merged_result():
    result = {'recognition_config': {'seal': ['paddle_v6']},
              'seal_check': {**dual_result(), 'local_channel': {
                  'status': '匹配', 'recognized': '正确本地章', 'reliable': True}}}
    assert labels({'seal_should_match': True}, result) == {}


@pytest.mark.parametrize('check', [
    {'backend': '本地 OCR'}, {'recognition_mode': 'local'},
    {'comparison_policy': 'strict_information_fields'},
])
def test_explicit_historical_local_provenance_is_preserved(check):
    result = {'seal_check': {**check, 'status': '不匹配', 'recognized': '旧本地错章'}}
    assert set(labels({'seal_should_match': True}, result)) == {'本地印章判定错误'}


def test_single_selected_provider_can_identify_an_unlabelled_historical_reading():
    result = {'recognition_config': {'seal': ['qingtong']},
              'seal_check': {'status': '不匹配', 'recognized': '旧清瞳错章'}}
    assert set(labels({'seal_should_match': True}, result)) == {'清瞳印章判定错误'}
    result['recognition_config']['seal'] = ['paddle_v6']
    assert set(labels({'seal_should_match': True}, result)) == {'本地印章判定错误'}


def test_unknown_explicit_method_does_not_inherit_a_local_configuration_hint():
    result = {'recognition_config': {'seal': ['paddle_v6']},
              'seal_check': {'method': 'unrecognized_service', 'status': '不匹配', 'recognized': '错章'}}
    assert set(labels({'seal_should_match': True}, result)) == {'印章判定错误'}


def test_explicit_qingtong_backend_precedes_a_shared_strict_comparison_policy():
    result = {'seal_check': {'backend': '清瞳印章 API', 'comparison_policy': 'strict_text',
                             'status': '不匹配', 'recognized': '清瞳错章'}}
    assert set(labels({'seal_should_match': True}, result)) == {'清瞳印章判定错误'}
