"""Persist contact-only seal corrections without rerunning provider recognition."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.domain.issues import issue
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from receipt_ocr.recognition.seal.postprocess.providers import combine_seal_provider_checks
from receipt_ocr.samples.preservation import refresh_sample_seal_contacts
from test_sample_center import samples
from test_sample_preserved_footer import applied_preserved_return


REQUIRED = '三星手机（南方韵和海雅）服务中心维修专用章'
PARTIAL = '三星手机服务中心维修专用章'
WRONG = '三星手机（南方韵和海雅）服务中心收货专用章'


def seal_variant(result, method):
    return next(item for item in result['recognition_variants']['seal'] if item['method'] == method)


def raw_result(service, result_id):
    with service.database.connect() as con:
        row = con.execute('SELECT result_json,original_result_json FROM results WHERE id=?',
                          (result_id,)).fetchone()
    return json.loads(row['result_json']), row['original_result_json']


def old_hotline_sample(samples, suffix='维修热线:82533861'):
    service, item, _ = applied_preserved_return(samples)
    current, _ = raw_result(service, item['result_id'])
    text = '三星手机(南方韵和海雅)服务中心 维修专用章 ' + suffix
    current['fields']['签章要求'] = REQUIRED
    acceptance = current['recognition_config'].setdefault('acceptance', {})
    acceptance.update(seal_match_mode='any', seal_pass_standard='any_exact')
    local = compare_seal_text_strict(REQUIRED, [WRONG])
    local.update(backend='本地 OCR', source='本地 OCR', simulated=False)
    partial = compare_seal_text_strict(REQUIRED, [PARTIAL])
    qingtong = {**partial, 'source': '清瞳 · 印章文字 OCR', 'backend': '清瞳',
        'dual_check': {'selected': {
            'template': {'label': PARTIAL, 'matched': False, 'requirement_match': deepcopy(partial)},
            'ocr': {'text': PARTIAL, 'matched': False, 'comparison': deepcopy(partial)},
        }}, 'api': {'ok': True, 'raw': '保留的清瞳返回'}}
    old_check = {'requirement': REQUIRED, 'recognized': text, 'all_recognized': [text],
        'status': '不匹配', 'reliable': True, 'score': .78, 'requirement_coverage': 1,
        'comparison_policy': 'strict_text', 'field_comparison': None,
        'message': '收货章内容与签章要求存在错字、多字或顺序差异',
        'source': '单证通', 'backend': '单证通', 'recognition_mode': 'danzhengtong', 'simulated': False}
    trace = current['danzhengtong']
    trace['result']['commitResult']['收货客户印章']['value'] = text
    trace['result']['commitResult']['签章要求']['value'] = REQUIRED
    for method, check in [('paddle_v6', local), ('qingtong', qingtong), ('danzhengtong', old_check)]:
        variant = seal_variant(current, method)
        variant.update(error=None, value=[check['recognized'], check['status']])
        variant['details']['seal_check'] = check
        if method == 'danzhengtong':
            variant['details']['danzhengtong'] = deepcopy(trace)
    current['seal_check'] = combine_seal_provider_checks([local, qingtong, old_check], match_mode='any')
    current['review_issues'] = [entry for entry in current.get('review_issues', []) if entry['scope'] != 'seal']
    current['review_issues'].append(issue('evidence_unreliable', 'seal', '客户印章尚未可靠识别'))
    current['review_reasons'] = list(dict.fromkeys(entry['message'] for entry in current['review_issues']))
    current.update(overall='需人工复核', final_result='需人工复核', review_status='待复核')
    with service.database.connect() as con:
        payload = json.dumps(current, ensure_ascii=False)
        con.execute("""UPDATE results SET result_json=?,original_result_json=?,overall='需人工复核',
            final_result='需人工复核',review_status='待复核' WHERE id=?""", (payload, payload, item['result_id']))
        con.execute('UPDATE danzhengtong_jobs SET result_json=? WHERE job_id=?',
                    (json.dumps(trace['result'], ensure_ascii=False), item['job_id']))
    return service, item, current


@pytest.mark.parametrize('suffix', [
    '维修热线:82533861', '*维修热线:82533861*',
    '维修热线：82533861', '维修热线：0755-82533861',
])
def test_persisted_hotline_refresh_keeps_evidence_and_updates_all_sample_views(samples, suffix):
    _, client = samples
    service, item, before = old_hotline_sample(samples, suffix)
    _, original_before = raw_result(service, item['result_id'])
    remote_before = service.store.remote_detail(item['job_id'])
    history_before = service.database.history(item['result_id'])
    job_before = service.store.get(item['job_id'])
    assert refresh_sample_seal_contacts(service.store) == 1
    result, original_after = raw_result(service, item['result_id'])
    dzt = seal_variant(result, 'danzhengtong')
    check = dzt['details']['seal_check']
    assert check['status'] == '匹配' and check['reliable'] is True
    assert check['recognized'] == seal_variant(before, 'danzhengtong')['details']['seal_check']['recognized']
    assert check['all_recognized'] == [check['recognized']]
    assert check['field_comparison']['additional_fields'] == ['电话']
    assert dzt['value'] == [check['recognized'], '匹配']
    assert result['seal_check']['status'] == '匹配' and result['seal_check']['reliable'] is True
    assert result['seal_check']['recognized'] == check['recognized']
    for method in ('paddle_v6', 'qingtong'):
        assert seal_variant(result, method) == seal_variant(before, method)
    for key in ('fields', 'date_check', 'signature_footer', 'danzhengtong', 'processing_artifacts'):
        assert result[key] == before[key]
    for stage in ('fields', 'products', 'handwriting', 'date'):
        assert result['recognition_variants'][stage] == before['recognition_variants'][stage]
    assert original_after == original_before
    assert service.store.remote_detail(item['job_id']) == remote_before
    assert service.store.get(item['job_id'])['branch_snapshot_json'] == job_before['branch_snapshot_json']
    history = service.database.history(item['result_id'])
    assert len(history) == len(history_before) + 1
    assert history[0]['action'] == '样单热线印章比对更新'
    assert seal_variant(json.loads(history[0]['before_json']), 'danzhengtong')['details']['seal_check']['status'] == '不匹配'
    assert seal_variant(json.loads(history[0]['after_json']), 'danzhengtong')['details']['seal_check']['status'] == '匹配'
    assert refresh_sample_seal_contacts(service.store) == 0
    assert raw_result(service, item['result_id'])[0] == result
    assert service.database.history(item['result_id']) == history

    detail = client.get(f"/api/samples/results/{item['result_id']}").get_json()
    listing = next(row for row in client.get('/api/samples').get_json()['items'] if row['filename'] == 'one.png')
    stats = next(row for row in client.get('/api/samples/statistics').get_json()['items'] if row['filename'] == 'one.png')
    assert detail['seal_check']['status'] == '匹配'
    assert seal_variant(detail, 'danzhengtong')['details']['seal_check']['status'] == '匹配'
    filters = listing['result_filters']['seals']
    assert filters['danzhengtong']['state'] == filters['combined']['state'] == '匹配'
    assert filters['danzhengtong']['value'] == filters['combined']['value'] == check['recognized']
    assert filters['qingtong_ocr']['state'] == stats['seals']['qingtong_ocr']['decision'] == '部分匹配'
    assert filters['local:paddle_v6']['state'] == stats['seals']['local:paddle_v6']['decision'] == '不匹配'


@pytest.mark.parametrize('guard', [
    'human_note', 'human_override', 'human_decision', 'human_field', 'human_seal', 'human_history',
    'waiting', 'received', 'lease', 'not_latest',
])
def test_hotline_refresh_preserves_reviewed_pending_and_superseded_records(samples, guard):
    service, item, current = old_hotline_sample(samples)
    with service.database.connect() as con:
        if guard == 'human_note':
            con.execute("UPDATE results SET human_note='人工备注' WHERE id=?", (item['result_id'],))
        elif guard == 'human_decision':
            con.execute("UPDATE results SET review_status='确认不通过',final_result='不通过' WHERE id=?", (item['result_id'],))
        elif guard in {'human_override', 'human_field', 'human_seal'}:
            if guard == 'human_override':
                current['page_review_override'] = {'fields': {'签章要求': '人工修改'}}
            elif guard == 'human_field':
                current['field_metadata']['签章要求'] = {'source': '人工复核', 'value': REQUIRED}
            else:
                current['seal_check']['human_confirmed_match'] = False
            con.execute('UPDATE results SET result_json=? WHERE id=?', (json.dumps(current, ensure_ascii=False), item['result_id']))
        elif guard in {'waiting', 'received'}:
            con.execute('UPDATE danzhengtong_jobs SET status=? WHERE job_id=?', (guard, item['job_id']))
        elif guard == 'lease':
            con.execute("UPDATE danzhengtong_jobs SET lease_until='2999-01-01T00:00:00+08:00' WHERE job_id=?", (item['job_id'],))
    if guard == 'human_history':
        service.database.review_result(item['result_id'], result=current, review_status='待复核',
            final_result='需人工复核', note='', action='人工修改印章')
    elif guard == 'not_latest':
        settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
        service.store.enqueue(['one.png'], {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
            'recognition_config': settings['recognition_config']}, settings)
    before = raw_result(service, item['result_id'])
    history_before = service.database.history(item['result_id'])
    remote_before = service.store.remote_detail(item['job_id'])
    job_before = service.store.get(item['job_id'])
    assert refresh_sample_seal_contacts(service.store) == 0
    assert raw_result(service, item['result_id']) == before
    assert service.database.history(item['result_id']) == history_before
    assert service.store.remote_detail(item['job_id']) == remote_before
    assert service.store.get(item['job_id']) == job_before
