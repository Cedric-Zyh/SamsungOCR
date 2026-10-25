import json
import sqlite3
from copy import deepcopy

import pytest

from receipt_ocr.web.sample_result_filters import (
    FILTER_RESULT_SQL, LIST_RESULT_SQL, decode_list_result, sample_result_filters,
)
from receipt_ocr.web.samples import SampleCenter
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from test_sample_center import samples


PLAN = {'date': ['paddle_v6'], 'seal': ['qingtong', 'paddle_v6', 'danzhengtong']}
TRUTH = {'actual_date': '2025-02-13', 'seal_should_match': False}


def project(result, *, status='succeeded', plan=PLAN):
    # Exercise the actual SQL allowlist used by the list endpoint.
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE results (result_json TEXT)')
        con.execute('INSERT INTO results VALUES(?)', (json.dumps(result),))
        compact = con.execute('SELECT ' + LIST_RESULT_SQL + ' FROM results r').fetchone()[0]
    return sample_result_filters({'status': status,
                                 'payload_json': json.dumps({'sample_options': {'recognition_config': plan}})},
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


def test_provider_verdicts_are_independent_and_accuracy_is_separate_from_requirement_matching():
    row, _ = project(evidence())
    assert row['date'] == {'state': '不匹配', 'value': '2025年2月13日', 'recognized': True, 'accuracy': '正确'}
    seals = row['seals']
    assert seals['combined']['state'] == '匹配'
    assert seals['qingtong_template']['state'] == '匹配'
    assert seals['qingtong_template']['accuracy'] == '错误'
    assert seals['qingtong_ocr']['state'] == '不匹配'
    assert seals['qingtong_ocr']['accuracy'] == '正确'
    assert seals['local:paddle_v6']['state'] == '需人工复核'
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
    assert row['seals']['local:paddle_v6']['state'] == '需人工复核'
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
    center.store.finish(center.store.claim(), evidence())
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
