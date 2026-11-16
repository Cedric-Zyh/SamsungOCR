import json
import sqlite3
from copy import deepcopy

import pytest

from receipt_ocr.samples.result_filters import (
    FILTER_RESULT_SQL, LIST_RESULT_SQL, decode_list_result, sample_result_filters, project_sample_seal_result,
)
from sample_support import SampleCenter
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from test_sample_center import samples


PLAN = {'date': ['paddle_v6'], 'seal': ['qingtong', 'paddle_v6', 'danzhengtong']}
TRUTH = {'actual_date': '2025-02-13', 'seal_should_match': False}


def project(result, *, status='succeeded', plan=PLAN, preserve=False, ensure_ascii=True):
    # Exercise the actual SQL allowlist used by the list endpoint.
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE results (result_json TEXT)')
        con.execute('INSERT INTO results VALUES(?)', (json.dumps(result, ensure_ascii=ensure_ascii),))
        compact = con.execute('SELECT ' + LIST_RESULT_SQL + ' FROM results r').fetchone()[0]
    return sample_result_filters({'status': status,
                                 'payload_json': json.dumps({'sample_options': {
                                     'recognition_config': plan, '_preserve_provider_results': preserve}})},
                                 TRUTH, result=decode_list_result(compact)), compact


def evidence():
    return {
        'date_check': {'status': '不匹配', 'actual': '2025年2月13日'},
        'seal_check': {'status': '匹配', 'recognized': '综合章'},
        'recognition_variants': {'seal': [
            {'method': 'qingtong', 'details': {'seal_check': {
                'status': '匹配', 'dual_check': {'selected': {
                    'template': {'label': '客户甲收货章', 'requirement_match': {'status': '匹配'}},
                    'ocr': {'text': '客户乙收货章', 'comparison': {'status': '不匹配'}},
                }}}}},
            {'method': 'paddle_v6', 'details': {'seal_check': {
                'status': '匹配', 'recognized': '本地待确认文字', 'reliable': False}}},
            {'method': 'danzhengtong', 'details': {'seal_check': {
                'status': '部分匹配', 'recognized': '客户甲'}}},
        ]},
    }


def date_and_signature_evidence():
    result = evidence()
    result.update(fields={'仓库联系人': '王五', '仓库接收人': '李四'},
                  signature_check={'contact': '王五', 'receiver': '李四', 'status': '匹配',
                                   'source': '人工复核', 'human_confirmed_match': True},
                  date_check={'status': '匹配', 'actual': '2025-02-13'})
    result['recognition_variants'].update(date=[
        {'method': 'paddle_v6', 'details': {'date_check': {'status': '不匹配', 'actual': '2025-02-12'}}},
        {'method': 'danzhengtong', 'details': {'date_check': {'status': '匹配', 'actual': '2025-02-13'}}},
    ], handwriting=[
        {'method': 'paddle_v6', 'value': {'仓库接收人': '李四'},
         'details': {'handwriting_fields': {'仓库接收人': '李四'}}},
        {'method': 'danzhengtong', 'value': {'仓库接收人': '王五'},
         'details': {'handwriting_fields': {'仓库接收人': '王五'}}},
    ])
    return result


MULTI_PLAN = {**PLAN, 'date': ['paddle_v6', 'danzhengtong'],
              'handwriting': ['paddle_v6', 'danzhengtong']}


@pytest.mark.parametrize('ensure_ascii', [True, False])
def test_date_and_signature_sources_keep_provider_readings_independent_of_combined_and_human_decisions(ensure_ascii):
    result = date_and_signature_evidence()
    before = deepcopy(result)
    row, _ = project(result, plan=MULTI_PLAN, ensure_ascii=ensure_ascii)
    assert row['date'] == row['dates']['combined']
    assert row['dates']['local:paddle_v6'] == {
        'state': '不匹配', 'value': '2025-02-12', 'recognized': True, 'accuracy': '错误'}
    assert row['dates']['danzhengtong']['state'] == '匹配'
    assert row['dates']['danzhengtong']['accuracy'] == '正确'
    assert row['signatures']['combined']['state'] == '匹配'
    assert row['signatures']['local:paddle_v6']['state'] == '不匹配'
    assert row['signatures']['local:paddle_v6']['value'] == '李四'
    assert row['signatures']['danzhengtong']['state'] == '匹配'
    assert row['signatures']['danzhengtong']['value'] == '王五'
    assert result == before


@pytest.mark.parametrize('ensure_ascii', [True, False])
def test_signature_reads_provider_fields_and_receiver_failure_metadata_in_both_stored_encodings(ensure_ascii):
    result = date_and_signature_evidence()
    result['recognition_variants']['handwriting'] = [
        {'method': 'paddle_v6', 'details': {'fields': {'仓库联系人': '王五、李四', '仓库接收人': '李四'}}},
        {'method': 'danzhengtong', 'details': {'handwriting_fields': {'仓库接收人': '王五'},
            'handwriting_metadata': {'仓库接收人': {'error': '接收人识别失败'}}}},
    ]
    row, _ = project(result, plan=MULTI_PLAN, ensure_ascii=ensure_ascii)
    assert row['signatures']['local:paddle_v6']['state'] == '匹配'
    assert row['signatures']['local:paddle_v6']['value'] == '李四'
    assert row['signatures']['danzhengtong']['state'] == '调用失败'
    assert row['signatures']['danzhengtong']['value'] == ''


def test_empty_signature_does_not_borrow_the_other_provider_and_missing_results_remain_distinct():
    result = date_and_signature_evidence()
    result['recognition_variants']['handwriting'][0].update(
        value={'仓库接收人': ''}, details={'handwriting_fields': {'仓库接收人': ''}})
    result['recognition_variants']['handwriting'][1].update(value=None, details=None)
    row, _ = project(result, plan=MULTI_PLAN)
    assert row['signatures']['local:paddle_v6']['state'] == '未识别'
    assert row['signatures']['local:paddle_v6']['value'] == ''
    assert row['signatures']['danzhengtong']['state'] == '缺少结果'
    # A completed empty object is still a real unreadable observation.
    result['recognition_variants']['handwriting'][0].update(value={}, details={'handwriting_fields': {}})
    row, _ = project(result, plan=MULTI_PLAN)
    assert row['signatures']['local:paddle_v6']['state'] == '未识别'


@pytest.mark.parametrize('stage', ['date', 'handwriting'])
@pytest.mark.parametrize('marker,expected', [
    ({'error': '接口失败'}, '调用失败'), ({'pending': True}, '测试中'),
    ({'execution_status': 'skipped'}, '未运行'), ({'simulated': True}, '缺少结果'),
])
def test_source_filter_excludes_failed_pending_skipped_and_simulated_observations(stage, marker, expected):
    result = date_and_signature_evidence()
    result['recognition_variants'][stage][1].update(marker)
    row, _ = project(result, plan=MULTI_PLAN)
    cells = row['dates' if stage == 'date' else 'signatures']
    assert cells['danzhengtong']['state'] == expected
    assert cells['danzhengtong']['value'] == ''
    assert cells['local:paddle_v6']['state'] == '不匹配'


def test_source_filter_uses_preserved_provider_config_and_does_not_expose_disabled_providers():
    result = date_and_signature_evidence()
    row, _ = project(result, plan={**MULTI_PLAN, 'date': ['paddle_v6'], 'handwriting': ['paddle_v6']})
    assert row['dates']['danzhengtong']['state'] == '未运行'
    assert row['signatures']['danzhengtong']['state'] == '未运行'
    result['recognition_config'] = MULTI_PLAN
    row, _ = project(result, plan={'date': ['danzhengtong'], 'handwriting': ['danzhengtong']}, preserve=True)
    assert row['dates']['local:paddle_v6']['value'] == '2025-02-12'
    assert row['signatures']['local:paddle_v6']['value'] == '李四'


def test_new_source_summaries_exclude_raw_provider_responses_images_and_unowned_fields():
    result = date_and_signature_evidence()
    for stage in ('date', 'handwriting'):
        for variant in result['recognition_variants'][stage]:
            variant['details'].update(processing_artifacts={'image': 'large-private-image'},
                                      danzhengtong={'mode': 'real', 'status': 'completed',
                                                   'result': {'raw': 'large-private-provider-response'}},
                                      fields={'unknown': 'unowned-private-field'})
    _, compact = project(result, plan=MULTI_PLAN)
    assert all(value not in compact for value in (
        'large-private-image', 'large-private-provider-response', 'unowned-private-field'))
    assert len(compact) < 10000


def test_legacy_merged_results_are_not_misattributed_to_every_selected_provider():
    result = {'fields': {'仓库联系人': '甲', '仓库接收人': '甲'},
              'date_check': {'status': '匹配', 'actual': '2025-02-13'}}
    row, _ = project(result, plan=MULTI_PLAN)
    assert row['dates']['combined']['state'] == '匹配'
    assert row['signatures']['combined']['state'] == '匹配'
    for method in ('local:paddle_v6', 'danzhengtong'):
        assert row['dates'][method]['state'] == '缺少结果'
        assert row['signatures'][method]['state'] == '缺少结果'


def test_source_options_are_carried_by_sample_listing(samples):
    _, client = samples
    data = client.get('/api/samples').json
    expected = {'combined', 'local:paddle_v6', 'danzhengtong'}
    for key in ('filter_date_sources', 'filter_signature_sources'):
        assert expected <= {row['key'] for row in data[key]}
        assert not any('清瞳' in row['label'] for row in data[key])


def test_provider_verdicts_are_independent_and_accuracy_is_separate_from_requirement_matching():
    row, _ = project(evidence())
    assert row['date'] == {'state': '不匹配', 'value': '2025年2月13日', 'recognized': True, 'accuracy': '正确'}
    seals = row['seals']
    assert seals['combined']['state'] == '匹配'
    assert seals['qingtong_template']['state'] == '匹配'
    assert seals['qingtong_template']['accuracy'] == '错误'
    assert seals['qingtong_ocr']['state'] == '不匹配'
    assert seals['qingtong_ocr']['accuracy'] == '正确'
    assert seals['local:paddle_v6']['state'] == '匹配'
    assert seals['local:paddle_seal']['state'] == '未运行'
    assert seals['danzhengtong']['state'] == '部分匹配'
    assert seals['danzhengtong']['accuracy'] == '错误'


def test_projection_excludes_images_observations_and_raw_provider_payloads():
    result = evidence()
    result['processing_artifacts'] = {'seals': [{'image': 'large-secret-image'}]}
    result['raw_text'] = 'private-entire-page'
    result['seal_check']['api'] = {'data': 'large-raw-payload'}
    result['recognition_variants']['seal'][0]['details']['processing_artifacts'] = {'image': 'large-secret-image'}
    row, compact = project(result)
    assert all(value not in compact for value in ['large-secret-image', 'private-entire-page', 'large-raw-payload'])
    assert row['seals']['qingtong_ocr']['value'] == '客户乙收货章'
    assert len(compact) < 6000


@pytest.mark.parametrize('status,expected', [('queued', '测试中'), ('running', '测试中'), ('', '未测试')])
def test_new_job_never_exposes_previous_recognition_evidence(status, expected):
    row, _ = project(evidence(), status=status)
    for cell in [row['date'], row['seals']['combined'], row['seals']['qingtong_ocr']]:
        assert cell['state'] == expected
        assert cell['value'] == ''
        assert cell['recognized'] is False
    if status:
        assert row['seals']['local:paddle_seal']['state'] == '未运行'


def test_failed_provider_unreadable_image_disabled_stage_and_missing_evidence_are_distinct():
    result = evidence()
    result['recognition_variants']['seal'][0] = {'method': 'qingtong', 'error': '接口超时', 'details': None}
    result['date_check'] = {'status': '未识别', 'actual': ''}
    row, _ = project(result)
    assert row['date']['state'] == '未识别'
    assert row['seals']['qingtong_ocr']['state'] == '调用失败'
    assert row['seals']['qingtong_ocr']['value'] == ''
    assert row['seals']['local:paddle_v6']['state'] == '匹配'
    row, _ = project({}, plan={'date': [], 'seal': ['qingtong']})
    assert row['date']['state'] == '未运行'
    assert row['seals']['qingtong_ocr']['state'] == '缺少结果'
    row, _ = project({}, status='failed')
    assert row['seals']['qingtong_ocr']['state'] == '调用失败'


def test_legacy_ocr_only_and_local_channel_use_their_own_evidence():
    result = {'seal_check': {'status': '匹配', 'recognized': '综合章',
        'dual_check': {'selected': {'ocr': {'text': '另一公司', 'comparison': {'status': '不匹配'}}}},
        'local_channel': {'status': '部分匹配', 'recognized': '本地文字'}}}
    row, _ = project(result, plan=None)
    assert row['seals']['qingtong_ocr']['state'] == '不匹配'
    assert row['seals']['local:legacy_local']['state'] == '部分匹配'
    assert row['seals']['local:paddle_v6']['state'] == '未运行'


def test_matching_provider_outranks_review_but_does_not_change_other_channels():
    result = evidence()
    result['seal_check'] = {'status': '需人工复核', 'recognized': '客户甲', 'reliable': False}
    result['recognition_variants']['seal'][1]['details']['seal_check'] = {
        'status': '部分匹配', 'recognized': '客户甲', 'reliable': False}
    row, _ = project(result)
    assert row['seals']['combined']['state'] == '匹配'
    assert row['seals']['combined']['value'] == '客户甲收货章'
    assert row['seals']['local:paddle_v6']['state'] == '部分匹配'
    assert row['seals']['qingtong_ocr']['state'] == '不匹配'
    assert all(cell['state'] != '需人工复核' for cell in row['seals'].values())


@pytest.mark.parametrize('value,expected', [
    ('客户甲有限公司收货专用章(1)', '匹配'),
    ('客户甲有限公司收货专用章', '部分匹配'),
    ('客户乙有限公司收货专用章(1)', '不匹配'), ('', '不匹配'),
])
def test_completed_review_status_is_resolved_using_actual_text(value, expected):
    requirement = '客户甲有限公司收货专用章(1)'
    result = {'fields': {'签章要求': requirement}, 'seal_check': {
        'status': '需人工复核', 'recognized': value, 'reliable': False}}
    row, _ = project(result, plan={'seal': ['paddle_v6']})
    assert row['seals']['combined']['state'] == expected


@pytest.mark.parametrize('extra', [
    {'human_confirmed_match': False},
    {'comparison_policy': 'manual_standard_text_and_shape'},
])
def test_explicit_human_or_shape_rejection_is_not_an_unresolved_review(extra):
    result = evidence()
    result['seal_check'] = {'status': '不匹配', 'recognized': '综合章', **extra}
    row, _ = project(result)
    assert row['seals']['combined']['state'] == '不匹配'


def test_disabled_or_failed_matching_channel_cannot_promote_the_combined_result():
    result = evidence()
    result['seal_check'] = {'status': '部分匹配', 'recognized': '客户甲'}
    result['recognition_variants']['seal'][1]['details']['seal_check']['status'] = '部分匹配'
    row, _ = project(result, plan={'seal': ['paddle_v6']})
    assert row['seals']['combined']['state'] == '部分匹配'
    result['recognition_variants']['seal'][0]['error'] = '接口失败'
    row, _ = project(result)
    assert row['seals']['combined']['state'] == '部分匹配'


def test_detail_projection_uses_same_priority_without_rewriting_saved_evidence_or_date():
    result = evidence()
    result['seal_check'] = {'status': '需人工复核', 'recognized': '客户甲', 'reliable': False}
    result['review_issues'] = [{'scope': 'seal', 'message': '印章需人工复核'},
                               {'scope': 'date', 'message': '日期未识别'}]
    before = deepcopy(result)
    detail = project_sample_seal_result(result)
    assert detail['seal_check']['status'] == '匹配'
    assert detail['seal_check']['recognized'] == '客户甲收货章'
    assert detail['seal_check']['reliable'] is True
    assert detail['review_issues'] == [{'scope': 'date', 'message': '日期未识别'}]
    assert detail['date_check'] == result['date_check']
    assert result == before


def test_legacy_unscoped_date_warnings_are_not_erased_by_a_sample_seal_match():
    result = evidence()
    result['review_reasons'] = ['签收日期尚未识别']
    detail = project_sample_seal_result(result)
    assert detail['review_reasons'] == result['review_reasons']


@pytest.mark.parametrize('result', [
    None, {}, evidence(),
    {'recognition_variants': {'seal': None}},
    {'recognition_variants': {'seal': []}},
    {'date_check': None, 'seal_check': None, 'recognition_variants': None},
    {'date_check': 'old-invalid-shape', 'seal_check': False, 'recognition_variants': 'legacy'},
    {'seal_check': {'status': True, 'recognized': False, 'reliable': False,
                    'dual_check': {'selected': {'template': {'matched': True}}}}},
    {'recognition_config': {'date': [], 'seal': ['paddle_v6']},
     'recognition_variants': {'seal': [{'method': 'paddle_v6', 'error': None, 'details': None}]}},
])
def test_compact_projection_preserves_legacy_sql_evidence_including_missing_and_null_variants(result):
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE results (result_json TEXT)')
        con.execute('INSERT INTO results VALUES(?)', (json.dumps(result) if result is not None else None,))
        legacy, compact = con.execute('SELECT ' + FILTER_RESULT_SQL + ',' + LIST_RESULT_SQL + ' FROM results r').fetchone()
    expected = json.loads(legacy)
    actual = decode_list_result(compact)
    del actual['fields'], actual['signature_check']
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_compact_projection_preserves_signature_review_and_recognition_config():
    result = evidence()
    result['fields'] = {'仓库联系人': '张三', '仓库接收人': '李四'}
    result['signature_check'] = {'source': '人工复核', 'contact': '张三', 'receiver': '李四',
                                 'human_confirmed_match': True, 'status': '匹配'}
    result['recognition_config'] = PLAN
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE results (result_json TEXT)')
        con.execute('INSERT INTO results VALUES(?)', (json.dumps(result),))
        projected = decode_list_result(con.execute('SELECT ' + LIST_RESULT_SQL + ' FROM results r').fetchone()[0])
    assert projected['fields'] == result['fields']
    assert projected['signature_check'] == result['signature_check']
    assert projected['recognition_config'] == PLAN


def test_list_endpoint_uses_latest_sample_job_and_carries_only_compact_filter_data(samples):
    ctx, client = samples
    center = SampleCenter(ctx)
    options = {'ocr_backend': 'paddle_v6', 'seal_recognition_mode': 'qingtong', 'recognition_config': PLAN}
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    center.store.enqueue(['one.png'], options, settings)
    job = center.store.claim()
    center.store.finish(job, evidence())
    # This fixture represents a fully returned test, including its remote lane.
    with center.database.connect() as con:
        con.execute("UPDATE danzhengtong_jobs SET status='applied' WHERE job_id=?", (job['id'],))
    data = client.get('/api/samples').json
    item = next(row for row in data['items'] if row['filename'] == 'one.png')
    assert item['result_filters']['seals']['qingtong_ocr']['state'] == '不匹配'
    assert item['result_filters']['date']['accuracy'] == '待标注'
    assert 'processing_artifacts' not in item
    assert {'qingtong_template', 'qingtong_ocr', 'danzhengtong'} <= {s['key'] for s in data['filter_seal_sources']}
    center.store.enqueue(['one.png'], options, settings)
    item = next(row for row in client.get('/api/samples').json['items'] if row['filename'] == 'one.png')
    assert item['result_filters']['date']['state'] == '测试中'
    assert item['result_filters']['seals']['qingtong_ocr']['value'] == ''
    assert ctx.database.list_results() == []
