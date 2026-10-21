import pytest

from receipt_ocr.web.samples import SampleCenter
from test_sample_center import samples


def save(client, state='pending', company='甲公司', note='本地漏字', **changes):
    data = client.get('/api/samples').json
    current = data['company_marks'].get(company, {})
    return client.post('/api/samples/company-mark', json={
        'company': company, 'state': state, 'note': note,
        'revision': current.get('revision', 0), 'reviewed': state == 'checked',
        'result_version': data['company_versions'].get(company), **changes})


def finish(ctx, client, name='one.png', error=''):
    client.post('/api/samples/run', json={'filenames': [name]})
    center = SampleCenter(ctx)
    center.store.finish(center.store.claim(), {'fields': {}, 'overall': '通过'}, error=error)


def test_company_marks_and_notes_persist_separately_and_can_be_cleared(samples):
    ctx, client = samples
    response = save(client)
    assert response.status_code == 200
    assert response.json['state'] == 'pending'
    assert response.json['note'] == '本地漏字'
    assert SampleCenter(ctx).listing()['company_marks']['甲公司']['note'] == '本地漏字'
    save(client, 'issue', company='乙公司', note='日期位置需确认')
    assert save(client, 'checked').json['state'] == 'checked'
    assert not client.get('/api/samples').json['company_marks']['甲公司']['has_new_results']
    cleared = save(client, 'unmarked', note='')
    assert cleared.json['state'] == 'unmarked'
    assert cleared.json['note'] == ''
    assert client.get('/api/samples').json['company_marks']['乙公司']['state'] == 'issue'
    assert ctx.database.list_results() == []
    assert ctx.database.list_tasks() == []
    with ctx.database.connect() as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='sample_company_marks'").fetchone() is None


def test_new_results_hint_survives_note_edit_and_queued_rerun(samples):
    ctx, client = samples
    finish(ctx, client)
    assert save(client, 'checked').status_code == 200
    # An unrelated company cannot invalidate this company's checked marker.
    finish(ctx, client, name='three.png')
    assert not client.get('/api/samples').json['company_marks']['甲公司']['has_new_results']
    client.post('/api/samples/run', json={'filenames': ['one.png']})
    assert not client.get('/api/samples').json['company_marks']['甲公司']['has_new_results']
    service = SampleCenter(ctx)
    service.store.finish(service.store.claim(), error='识别失败')
    mark = client.get('/api/samples').json['company_marks']['甲公司']
    assert mark['state'] == 'checked' and mark['has_new_results']
    edited = save(client, 'checked', note='保留提示，稍后再看', reviewed=False).json
    assert edited['has_new_results']
    assert not save(client, 'checked').json['has_new_results']


def test_changed_results_and_concurrent_edits_cannot_be_silently_acknowledged(samples):
    ctx, client = samples
    observed = client.get('/api/samples').json['company_versions']['甲公司']
    finish(ctx, client)
    response = save(client, 'checked', result_version=observed)
    assert response.status_code == 409
    assert client.get('/api/samples').json['company_marks'] == {}
    assert save(client, 'checked').status_code == 200
    assert save(client, 'issue', revision=0).status_code == 409
    assert client.get('/api/samples').json['company_marks']['甲公司']['state'] == 'checked'


@pytest.mark.parametrize('changes', [
    {'state': '随便'}, {'state': []}, {'note': None}, {'note': '字' * 501},
    {'revision': -1}, {'revision': True}, {'reviewed': 'yes'},
    {'state': 'checked', 'reviewed': False},
])
def test_invalid_marker_does_not_write(samples, changes):
    _, client = samples
    assert save(client, **changes).status_code == 400
    assert client.get('/api/samples').json['company_marks'] == {}


def test_unknown_company_and_empty_request(samples):
    _, client = samples
    assert save(client, company='不存在的公司').status_code == 404
    assert client.post('/api/samples/company-mark', json=[]).status_code == 400


def test_company_seals_follow_annotated_requirements_and_refresh_on_registration(samples):
    import json
    ctx, client = samples
    truth = json.loads(ctx.GROUND_TRUTH_PATH.read_text())
    truth['one.png']['fields']['签章要求'] = ' 共用公司维修章 '
    truth['two.png']['fields']['签章要求'] = '另一公司专用章'
    truth['three.png']['fields']['签章要求'] = '共用公司维修章'
    ctx.GROUND_TRUTH_PATH.write_text(json.dumps(truth))

    def register(requirement):
        ctx.database.save_seal_standard(dict(requirement=requirement, image_name='seal.png',
            recognized=requirement, shape='圆形', color='红色', source_result_id=0,
            source_filename='one.png', candidate_id='test'), expected_revision=0)

    # A similar customer name alone must not count as a registered standard.
    register('甲公司专用章')
    assert not client.get('/api/samples').json['company_seals']['甲公司']['has_standard']
    register('共用公司维修章')
    seals = client.get('/api/samples').json['company_seals']
    assert seals['甲公司'] == dict(matched=1, total=2, unlabeled=0, has_standard=True, complete=False)
    assert seals['乙公司']['complete']
    assert seals['未分类'] == dict(matched=0, total=0, unlabeled=1, has_standard=False, complete=False)
    register('另一公司专用章')
    assert client.get('/api/samples').json['company_seals']['甲公司']['complete']
    truth['two.png']['fields'].pop('签章要求')
    ctx.GROUND_TRUTH_PATH.write_text(json.dumps(truth))
    partial = client.get('/api/samples').json['company_seals']['甲公司']
    assert partial['has_standard'] and partial['unlabeled'] == 1 and not partial['complete']
