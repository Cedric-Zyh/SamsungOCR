from copy import deepcopy
import json

import pytest

from receipt_ocr.web.samples import SampleCenter
from test_sample_center import samples


def completed(samples):
    ctx, client = samples
    service = SampleCenter(ctx)
    client.post('/api/samples/run', json={'filenames': ['one.png']})
    job = service.store.claim()
    machine = {
        'fields': {'客户名称': '机器客户', '签章要求': '机器要求'},
        'date_check': {'actual': '2026-03-27', 'status': '匹配', 'reliable': True},
        'seal_check': {'recognized': '机器章', 'status': '匹配', 'reliable': True},
        'recognition_variants': {'seal': [{'method': 'paddle_v6', 'details': {
            'seal_check': {'recognized': '机器章', 'status': '匹配'}}}]},
    }
    service.store.finish(job, deepcopy(machine))
    result_id = next(item['result_id'] for item in client.get('/api/samples').json['items']
                     if item['filename'] == 'one.png')
    return ctx, client, service, result_id


def test_annotation_save_is_durable_audited_and_separate_from_machine_and_production(samples):
    ctx, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    original_truth = ctx.GROUND_TRUTH_PATH.read_bytes()
    original_machine = service.database.get_result(result_id)
    truth = client.get(url + '/ground-truth').json
    assert 'seal_should_match' not in truth['entry']
    response = client.patch(url + '/annotations', json={
        'revision': truth['revision'], 'fields': {'客户名称': '人工客户', '签章要求': '人工要求', '要求到货': '2026-03-28'},
        'date_present': True, 'actual_date': '2026-03-28', 'seal_should_match': False,
        'human_note': '部门漏识别，需要核对', 'error_type': '印章识别错误',
    })
    assert response.status_code == 200
    saved = response.json
    assert saved['entry']['fields']['承运商'] == '承运乙'
    assert saved['entry']['human_note'] == '部门漏识别，需要核对'
    assert saved['entry']['seal_should_match'] is False
    assert saved['revision'] != truth['revision']
    assert len(saved['history']) == 1
    assert json.loads(saved['history'][0]['after_json']) == saved['entry']
    assert client.get(url + '/review-history').json[0]['action'] == '保存样单标注与备注'
    assert client.get(url + '/ground-truth').json == saved
    assert service.database.get_result(result_id) == original_machine
    assert client.get(url).json['fields']['客户名称'] == '机器客户'
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == original_truth
    assert ctx.database.list_results() == []
    assert ctx.database.ground_truth_history('one.png') == []
    statistics = next(item for item in client.get('/api/samples/statistics').json['items'] if item['filename'] == 'one.png')
    assert statistics['company'] == '人工客户'
    assert statistics['expected_date'] == '2026-03-28'
    assert statistics['expected_seal'] == '不匹配'
    assert statistics['date']['state'] == '错误'
    # A fresh recognition run and a fresh service both retain the human labels and notes.
    client.post('/api/samples/run', json={'filenames': ['one.png']})
    service.store.finish(service.store.claim(), error='测试失败')
    fresh = SampleCenter(ctx)
    assert fresh.catalog()['one.png'] == saved['entry']
    latest_id = next(item['result_id'] for item in client.get('/api/samples').json['items'] if item['filename'] == 'one.png')
    assert latest_id != result_id
    assert client.get(f'/api/samples/results/{latest_id}/ground-truth').json['entry'] == saved['entry']


def test_stale_annotation_save_and_deleted_sample_cannot_overwrite_labels(samples):
    _, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    truth = client.get(url + '/ground-truth').json
    payload = {'revision': truth['revision'], 'human_note': '第一窗口'}
    assert client.patch(url + '/annotations', json=payload).status_code == 200
    response = client.patch(url + '/annotations', json={**payload, 'human_note': '旧窗口'})
    assert response.status_code == 409
    assert response.json['code'] == 'sample_annotation_conflict'
    assert service.catalog()['one.png']['human_note'] == '第一窗口'
    assert len(service.database.ground_truth_history('one.png')) == 1
    assert client.patch('/api/samples/results/99999/annotations', json=payload).status_code == 404
    client.post('/api/samples/delete', json={'filenames': ['one.png']})
    assert client.patch(url + '/annotations', json=payload).status_code == 404


@pytest.mark.parametrize('values', [
    {'date_present': True, 'actual_date': '2026-02-30'},
    {'date_present': True, 'actual_date': ''},
    {'date_present': False, 'actual_date': '2026-03-27'},
    {'date_present': 'false'}, {'seal_should_match': 'false'},
    {'human_note': ['invalid']}, {'error_type': 'x' * 201},
    {'fields': {'客户名称': None}}, {'fields': {'unknown': 'value'}},
])
def test_invalid_annotation_is_atomic(samples, values):
    _, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    truth = client.get(url + '/ground-truth').json
    response = client.patch(url + '/annotations', json={
        'revision': truth['revision'], 'fields': {'客户名称': '不应保存'}, **values})
    assert response.status_code == 400
    assert client.get(url + '/ground-truth').json == truth
    assert service.database.ground_truth_history('one.png') == []


def test_blank_and_unlabeled_are_distinct_and_unrelated_labels_survive(samples):
    _, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    truth = client.get(url + '/ground-truth').json
    saved = client.patch(url + '/annotations', json={
        'revision': truth['revision'], 'date_present': False, 'actual_date': '', 'seal_should_match': False}).json
    assert saved['entry']['date_present'] is False
    assert saved['entry']['actual_date'] == ''
    saved = client.patch(url + '/annotations', json={
        'revision': saved['revision'], 'date_present': None, 'actual_date': '', 'seal_should_match': None}).json
    assert 'date_present' not in saved['entry']
    assert 'actual_date' not in saved['entry']
    assert 'seal_should_match' not in saved['entry']
    assert saved['entry']['fields'] == truth['entry']['fields']
    # Idempotent saves do not create duplicate audit events.
    client.patch(url + '/annotations', json={'revision': saved['revision']})
    assert len(service.database.ground_truth_history('one.png')) == 2
