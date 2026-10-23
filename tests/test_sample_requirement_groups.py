import json

import pytest

from receipt_ocr.domain.seal_standards import requirement_key
from receipt_ocr.web.samples import SampleCenter
from test_sample_center import samples
from test_sample_library import upload


def finish(ctx, client, name, requirement):
    assert client.post('/api/samples/run', json={'filenames': [name]}).status_code == 202
    center = SampleCenter(ctx)
    center.store.finish(center.store.claim(), {'fields': {'签章要求': requirement}, 'overall': '通过'})


def classify(client, company='甲公司'):
    return client.post('/api/samples/classify-requirements', json={'company': company})


def test_defaults_to_ungrouped_and_classifies_multiple_requirements_per_company(samples):
    ctx, client = samples
    truth = json.loads(ctx.GROUND_TRUTH_PATH.read_text())
    truth['one.png']['fields']['签章要求'] = '保留原人工标注'
    ctx.GROUND_TRUTH_PATH.write_text(json.dumps(truth))
    before = ctx.GROUND_TRUTH_PATH.read_bytes()
    initial = client.get('/api/samples').json
    assert all(not item['requirement_key'] for item in initial['items'])
    assert initial['requirement_groups']['甲公司'] == [
        {'key': '', 'name': '未归类', 'count': 2, 'has_standard': False}]
    finish(ctx, client, 'one.png', '甲公司公章')
    finish(ctx, client, 'two.png', '第三方收货专用章')
    finish(ctx, client, 'three.png', '乙公司公章')
    response = classify(client)
    assert response.status_code == 200
    assert response.json['created_groups'] == ['甲公司公章', '第三方收货专用章']
    assert response.json['remaining'] == 0
    restarted = SampleCenter(ctx)
    catalog = restarted.catalog()
    assert catalog['one.png']['requirement_group'] == '甲公司公章'
    assert catalog['one.png']['fields']['签章要求'] == '保留原人工标注'
    assert 'requirement_group' not in catalog['three.png']
    groups = restarted.listing()['requirement_groups']['甲公司']
    assert {group['name'] for group in groups} == {'甲公司公章', '第三方收货专用章'}
    assert sum(group['count'] for group in groups) == 2
    assert classify(client).json['classified'] == []
    finish(ctx, client, 'one.png', '重试识别成其他章')
    assert classify(client).json['classified'] == [
        {'filename': 'one.png', 'company': '甲公司', 'requirement': '重试识别成其他章'}]
    assert restarted.catalog()['one.png']['requirement_group'] == '重试识别成其他章'
    assert '甲公司公章' not in {group['name'] for group in restarted.listing()['requirement_groups']['甲公司']}
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == before
    assert ctx.database.list_results() == []


def test_groups_merge_by_normalized_requirement_and_standards_belong_to_requirement(samples):
    ctx, client = samples
    finish(ctx, client, 'one.png', '  甲公司公章  ')
    finish(ctx, client, 'two.png', '甲公司 公章')
    ctx.database.save_seal_standard({
        'requirement': '甲公司公章', 'image_name': 'test.png', 'recognized': '甲公司',
        'shape': 'round', 'color': 'red', 'source_result_id': 0,
        'source_filename': '', 'candidate_id': '', 'source_kind': 'sample'}, expected_revision=0)
    result = classify(client).json
    assert result['created_groups'] == ['甲公司公章']
    assert len(result['classified']) == 2
    groups = client.get('/api/samples').json['requirement_groups']
    assert groups['甲公司'] == [{'key': requirement_key('甲公司公章'), 'name': '甲公司公章',
                               'count': 2, 'has_standard': True}]
    assert groups['乙公司'][0]['has_standard'] is False


def test_missing_busy_and_failed_results_do_not_guess_a_requirement(samples):
    ctx, client = samples
    assert classify(client).json['remaining'] == 2
    finish(ctx, client, 'one.png', '')
    finish(ctx, client, 'two.png', '签收章')
    client.post('/api/samples/run', json={'filenames': ['two.png']})
    assert classify(client).json['classified'] == []
    center = SampleCenter(ctx)
    center.store.finish(center.store.claim(), error='失败')
    result = classify(client).json
    assert result['classified'] == []
    assert result['remaining'] == 2
    finish(ctx, client, 'two.png', '签收章')
    result = classify(client).json
    assert result['classified'] == [{'filename': 'two.png', 'company': '甲公司', 'requirement': '签收章'}]


def test_imported_sample_keeps_its_image_and_survives_restart_after_grouping(samples):
    ctx, client = samples
    upload(client, 'new.png', '甲公司')
    finish(ctx, client, 'new.png', '第三方公章')
    response = classify(client)
    assert response.json['classified'] == [{'filename': 'new.png', 'company': '甲公司', 'requirement': '第三方公章'}]
    restarted = SampleCenter(ctx)
    assert restarted.catalog()['new.png']['requirement_group'] == '第三方公章'
    assert restarted.source('new.png').is_file()
    assert client.get('/api/samples/image/new.png').status_code == 200
    assert client.get('/api/samples/statistics').status_code == 200


def test_customer_classification_also_classifies_requirement_in_one_step(samples):
    ctx, client = samples
    before = ctx.GROUND_TRUTH_PATH.read_bytes()
    client.post('/api/samples/run', json={'filenames': ['blank.png']})
    center = SampleCenter(ctx)
    center.store.finish(center.store.claim(), {'fields': {'客户名称': '新公司', '签章要求': '新公司收货章'}})
    response = client.post('/api/samples/classify')
    assert response.status_code == 200
    assert response.json['classified'] == [
        {'filename': 'blank.png', 'company': '新公司', 'requirement': '新公司收货章'}]
    assert response.json['created_companies'] == ['新公司']
    assert response.json['created_groups'] == ['新公司收货章']
    row = next(row for row in client.get('/api/samples').json['items'] if row['filename'] == 'blank.png')
    assert row['company'] == '新公司'
    assert row['requirement_key'] == requirement_key('新公司收货章')
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == before


def test_all_samples_classifies_only_latest_completed_records(samples):
    ctx, client = samples
    finish(ctx, client, 'one.png', '甲公司章')
    finish(ctx, client, 'two.png', '另一个章')
    client.post('/api/samples/run', json={'filenames': ['two.png']})
    finish_job = SampleCenter(ctx)
    finish_job.store.finish(finish_job.store.claim(), error='最新测试失败')
    client.post('/api/samples/run', json={'filenames': ['three.png']})
    response = client.post('/api/samples/classify', json={'company': ''})
    assert response.status_code == 200
    assert response.json['processed'] == 1
    assert response.json['skipped'] == 3
    assert [row['filename'] for row in response.json['classified']] == ['one.png']
    catalog = SampleCenter(ctx).catalog()
    assert catalog['one.png']['requirement_group'] == '甲公司章'
    assert 'requirement_group' not in catalog['two.png']
    assert 'requirement_group' not in catalog['three.png']


def test_selected_records_can_be_corrected_and_empty_requirement_groups_disappear(samples):
    ctx, client = samples
    finish(ctx, client, 'one.png', '旧要求')
    finish(ctx, client, 'two.png', '旧要求')
    classify(client)
    finish(ctx, client, 'one.png', '新要求')
    finish(ctx, client, 'two.png', '')
    response = client.post('/api/samples/classify-requirements', json={
        'company': '甲公司', 'filenames': ['one.png']})
    assert response.json['classified'] == [{'filename': 'one.png', 'company': '甲公司', 'requirement': '新要求'}]
    assert SampleCenter(ctx).catalog()['two.png']['requirement_group'] == '旧要求'
    response = client.post('/api/samples/classify-requirements', json={
        'company': '甲公司', 'requirement': requirement_key('旧要求')})
    assert response.json['classified'] == [{'filename': 'two.png', 'company': '甲公司', 'requirement': ''}]
    groups = client.get('/api/samples').json['requirement_groups']['甲公司']
    assert {group['name'] for group in groups} == {'新要求', '未归类'}
    assert all(group['count'] > 0 for group in groups)
    # A company with every sample assigned can still be checked again.
    finish(ctx, client, 'two.png', '新要求')
    classify(client)
    checked = classify(client).json
    assert checked['processed'] == 2 and checked['classified'] == []


@pytest.mark.parametrize('payload', [[], {'company': None}, {'requirement': []},
    {'filenames': []}, {'filenames': [3]}, {'filenames': ['missing.png']}])
def test_invalid_classification_scope_does_not_change_catalog(samples, payload):
    ctx, client = samples
    before = SampleCenter(ctx).catalog()
    assert client.post('/api/samples/classify', json=payload).status_code == 400
    assert SampleCenter(ctx).catalog() == before


@pytest.mark.parametrize('payload,status', [(None, 400), ({}, 400), ([], 400),
    ({'company': 3}, 400), ({'company': ''}, 400), ({'company': '不存在'}, 404)])
def test_invalid_company(samples, payload, status):
    _, client = samples
    assert client.post('/api/samples/classify-requirements', json=payload).status_code == status
