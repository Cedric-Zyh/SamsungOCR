"""Human problem observations remain separate from machine evidence and test runs."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.domain.sample_problems import resolve_problem_annotations
from test_sample_annotations import completed
from test_sample_center import samples


def test_multiselect_round_trip_is_deduplicated_audited_and_scoped_to_result(samples):
    ctx, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    before = client.get(url + '/ground-truth').json
    machine = deepcopy(service.database.get_result(result_id))
    assert before['problem_review'] == {'status': 'unmarked', 'types': []}
    assert '日期读错' in before['problem_options']['types']
    saved = client.patch(url + '/annotations', json={
        'revision': before['revision'], 'problem_status': 'issues',
        'problem_types': ['日期读错', '印章文字缺漏或读错', '日期读错'],
        'sample_features': ['遮挡', '印章过淡', '遮挡'], 'human_note': '日期22日误读02日；印章漏仓储部',
        'problem_context': {'result_id': 999, 'task_id': 'forged'},
    })
    assert saved.status_code == 200
    data = saved.json
    assert data['problem_review'] == {'status': 'issues', 'types': ['日期读错', '印章文字缺漏或读错']}
    assert data['sample_features'] == ['遮挡', '印章过淡']
    scoped = data['entry']['problem_reviews'][str(result_id)]
    assert scoped['result_id'] == result_id
    assert scoped['task_id'] == machine['task_id']
    assert scoped['task_id'] != 'forged'
    assert scoped['tested_at'] and scoped['methods']
    assert client.get(url + '/ground-truth').json == data
    assert json.loads(data['history'][0]['after_json']) == data['entry']
    assert service.database.get_result(result_id) == machine
    assert ctx.database.list_results() == []
    again = client.patch(url + '/annotations', json={
        'revision': data['revision'], 'problem_status': 'issues',
        'problem_types': data['problem_review']['types'], 'sample_features': data['sample_features'],
        'human_note': data['entry']['human_note'],
    }).json
    assert again['revision'] == data['revision'] and len(again['history']) == 1


def test_new_test_does_not_inherit_old_errors_but_keeps_features_and_notes(samples):
    _, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    before = client.get(url + '/ground-truth').json
    saved = client.patch(url + '/annotations', json={
        'revision': before['revision'], 'problem_status': 'issues', 'problem_types': ['日期读错'],
        'sample_features': ['遮挡'], 'human_note': '日期区域有章遮挡',
    }).json
    first_task = saved['problem_context']['task_id']
    client.post('/api/samples/run', json={'filenames': ['one.png']})
    service.store.finish(service.store.claim(), error='新一轮失败')
    new_id = next(row['result_id'] for row in client.get('/api/samples').json['items']
                  if row['filename'] == 'one.png')
    new_url = f'/api/samples/results/{new_id}'
    current = client.get(new_url + '/ground-truth').json
    assert new_id != result_id
    assert current['problem_review'] == {'status': 'unmarked', 'types': []}
    assert current['sample_features'] == ['遮挡']
    assert current['entry']['human_note'] == '日期区域有章遮挡'
    assert client.get(url + '/ground-truth').json['problem_review']['types'] == ['日期读错']
    latest_row = next(row for row in client.get('/api/samples/statistics').json['items'] if row['filename'] == 'one.png')
    old_row = client.get('/api/samples/statistics', query_string={'task_id': first_task}).json['items'][0]
    assert latest_row['problem_review']['status'] == 'unmarked'
    assert old_row['problem_review']['types'] == ['日期读错']
    clear = client.patch(new_url + '/annotations', json={
        'revision': current['revision'], 'problem_status': 'clear', 'problem_types': [],
    }).json
    assert clear['problem_review'] == {'status': 'clear', 'types': []}
    assert clear['sample_features'] == ['遮挡']
    assert len(clear['entry']['problem_reviews']) == 2
    reset = client.patch(new_url + '/annotations', json={
        'revision': clear['revision'], 'problem_status': 'unmarked', 'problem_types': [],
    }).json
    assert reset['problem_review']['status'] == 'unmarked'
    assert reset['entry']['human_note'] == '日期区域有章遮挡'


def test_problem_suggestions_use_saved_truth_without_saving_a_review_or_changing_machine(samples):
    _, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    before = client.get(url + '/ground-truth').json
    machine = deepcopy(service.database.get_result(result_id))
    saved_truth = client.patch(url + '/annotations', json={
        'revision': before['revision'], 'date_present': True, 'actual_date': '2026-03-28',
        'seal_should_match': False,
    }).json
    assert saved_truth['problem_review_saved'] is False
    assert saved_truth['problem_review'] == {'status': 'unmarked', 'types': []}
    assert {'日期读错', '本地印章判定错误'} <= {item['type'] for item in saved_truth['problem_suggestions']}
    assert all(item['reason'] for item in saved_truth['problem_suggestions'])
    assert 'problem_reviews' not in saved_truth['entry']
    assert client.get(url + '/ground-truth').json == saved_truth
    assert len(service.database.ground_truth_history('one.png')) == 1
    assert service.database.get_result(result_id) == machine
    # Suggestions are still available to manually reapply, but cannot replace an explicit clear decision.
    clear = client.patch(url + '/annotations', json={
        'revision': saved_truth['revision'], 'problem_status': 'clear', 'problem_types': [],
    }).json
    assert clear['problem_review_saved'] is True
    assert clear['problem_review'] == {'status': 'clear', 'types': []}
    assert clear['problem_suggestions'] == saved_truth['problem_suggestions']


def test_source_groups_and_source_specific_tags_round_trip_without_reclassifying_old_marks(samples):
    _, client, service, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    before = client.get(url + '/ground-truth').json
    groups = {group['label']: group['types'] for group in before['problem_options']['groups']}
    assert '清瞳印章判定错误' in groups['清瞳印章']
    assert '本地印章判定错误' in groups['本地印章']
    assert not any('印章判定错误' in types for types in groups.values())
    assert before['problem_result_summary'] == {'seal_status': '匹配', 'seal_reliable': True}
    machine = deepcopy(service.database.get_result(result_id))
    # An old mark remains unsourced until the reviewer explicitly selects a source.
    old = client.patch(url + '/annotations', json={
        'revision': before['revision'], 'problem_status': 'issues', 'problem_types': ['印章判定错误'],
    }).json
    assert old['problem_review']['types'] == ['印章判定错误']
    saved = client.patch(url + '/annotations', json={
        'revision': old['revision'], 'problem_status': 'issues',
        'problem_types': ['清瞳印章判定错误', '本地印章文字缺漏或读错', '清瞳印章判定错误'],
    }).json
    assert saved['problem_review']['types'] == ['清瞳印章判定错误', '本地印章文字缺漏或读错']
    assert saved['problem_result_summary']['seal_status'] == '匹配'
    assert client.get(url + '/ground-truth').json == saved
    assert service.database.get_result(result_id) == machine
    history = service.database.ground_truth_history('one.png')
    assert json.loads(history[0]['before_json'])['problem_reviews'][str(result_id)]['types'] == ['印章判定错误']


@pytest.mark.parametrize('values', [
    {'problem_status': 'issues', 'problem_types': []},
    {'problem_status': 'clear', 'problem_types': ['日期读错']},
    {'problem_status': 'unmarked', 'problem_types': ['日期读错']},
    {'problem_status': ['issues'], 'problem_types': ['日期读错']},
    {'problem_status': 'unknown', 'problem_types': []},
    {'problem_status': 'issues', 'problem_types': '日期读错'},
    {'problem_status': 'issues', 'problem_types': [None]},
    {'problem_status': 'issues', 'problem_types': ['不存在的类型']},
    {'problem_status': 'issues', 'problem_types': ['日期读错'] * 33},
    {'sample_features': '模糊'}, {'sample_features': ['不存在的特征']},
    {'sample_features': [True]},
])
def test_invalid_multiselect_is_atomic(samples, values):
    _, client, _, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    before = client.get(url + '/ground-truth').json
    response = client.patch(url + '/annotations', json={
        'revision': before['revision'], 'human_note': '不应写入', **values,
    })
    assert response.status_code == 400
    assert client.get(url + '/ground-truth').json == before


@pytest.mark.parametrize('legacy', ['印章识别错误', '自定义历史标签 <章&字>'])
def test_legacy_labels_are_preserved_without_guessing_a_test(samples, legacy):
    _, client, _, result_id = completed(samples)
    url = f'/api/samples/results/{result_id}'
    before = client.get(url + '/ground-truth').json
    legacy_saved = client.patch(url + '/annotations', json={
        'revision': before['revision'], 'error_type': legacy, 'human_note': '旧备注',
    }).json
    assert legacy_saved['problem_review']['status'] == 'unmarked'
    assert legacy_saved['legacy_problem_types'] == [legacy]
    saved = client.patch(url + '/annotations', json={
        'revision': legacy_saved['revision'], 'problem_status': 'issues',
        'problem_types': [legacy, '日期漏识别'], 'sample_features': ['模糊'],
    }).json
    assert saved['problem_review']['types'] == [legacy, '日期漏识别']
    assert saved['entry']['error_type'] == legacy
    assert saved['entry']['human_note'] == '旧备注'
    assert saved['legacy_problem_types'] == []
    assert resolve_problem_annotations(saved['entry'], result_id + 100)['legacy_problem_types'] == []
    cleared = client.patch(url + '/annotations', json={
        'revision': saved['revision'], 'problem_status': 'clear', 'problem_types': [],
    }).json
    assert cleared['legacy_problem_types'] == []
    assert cleared['entry']['error_type'] == legacy


def test_legacy_features_can_be_deliberately_cleared_and_reads_are_nonmutating():
    old = {'error_type': '图像质量问题', 'human_note': '旧备注'}
    before = deepcopy(old)
    assert resolve_problem_annotations(old, 1)['sample_features'] == ['图像质量问题']
    assert resolve_problem_annotations(old, 1)['legacy_problem_types'] == []
    assert old == before
    assert resolve_problem_annotations({**old, 'sample_features': []}, 1)['sample_features'] == []
    broken = {'problem_reviews': {'1': {'status': [], 'types': ['日期读错']}}}
    assert resolve_problem_annotations(broken, 1)['problem_review'] == {'status': 'unmarked', 'types': []}
    conflict = {'problem_reviews': {'1': {'status': 'clear', 'types': ['日期读错']}}}
    assert resolve_problem_annotations(conflict, 1)['problem_review'] == {'status': 'unmarked', 'types': []}


@pytest.mark.parametrize('payload', ['[]', 'broken-json', '{"sample_options":[]}',
                                     '{"recognition_config":{"date":"paddle_v6","seal":[null]}}'])
def test_historical_job_metadata_does_not_break_annotations(samples, payload):
    _, client, service, result_id = completed(samples)
    with service.database.connect() as con:
        con.execute('UPDATE recognition_jobs SET payload_json=? WHERE result_id=?', (payload, result_id))
    response = client.get(f'/api/samples/results/{result_id}/ground-truth')
    assert response.status_code == 200
    assert response.json['problem_context']['result_id'] == result_id
    assert response.json['problem_context']['methods'] == []
