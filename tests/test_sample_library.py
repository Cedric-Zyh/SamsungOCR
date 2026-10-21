from io import BytesIO

from PIL import Image
import pytest

from receipt_ocr.web.samples import SampleCenter
from receipt_ocr.web.sample_library import LibraryConflict
from test_sample_center import samples


def picture(color='white'):
    stream = BytesIO()
    Image.new('RGB', (32, 48), color).save(stream, 'PNG')
    stream.seek(0)
    return stream


def upload(client, name='new.png', company='新客户'):
    return client.post('/api/samples/import', data={'company': company, 'files': (picture(), name)})


def test_import_groups_customers_previews_and_persists_without_changing_dataset(samples):
    ctx, client = samples
    original_truth = ctx.GROUND_TRUTH_PATH.read_bytes()
    original_files = set(ctx.DATA_DIR.iterdir())
    response = upload(client)
    assert response.status_code == 201
    assert response.json['company'] == '新客户'
    rows = client.get('/api/samples').json['items']
    assert next(row for row in rows if row['filename'] == 'new.png')['company'] == '新客户'
    assert 'new.png' in SampleCenter(ctx).catalog()
    assert client.get('/api/samples/image/new.png').status_code == 200
    assert client.get('/api/samples/image/new.png?thumbnail=1').status_code == 200
    stats = client.get('/api/samples/statistics').json['items']
    assert next(row for row in stats if row['filename'] == 'new.png')['expected_date'] is None
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == original_truth
    assert set(ctx.DATA_DIR.iterdir()) == original_files
    assert ctx.database.list_tasks() == [] and ctx.database.list_results() == []


def test_import_names_never_overwrite_existing_or_deleted_samples(samples):
    ctx, client = samples
    first = upload(client, 'one.png', '').json['imported'][0]['filename']
    assert first == 'one (2).png'
    assert client.get('/api/samples').json['items'][-1]['company'] == '未分类'
    assert client.post('/api/samples/delete', json={'filenames': [first]}).status_code == 200
    second = upload(client, 'one.png').json['imported'][0]['filename']
    assert second == 'one (3).png'
    response = client.post('/api/samples/import', data={'files': [(picture(), 'duplicate.png'), (picture(), 'duplicate.png')]})
    assert [row['filename'] for row in response.json['imported']] == ['duplicate.png', 'duplicate (2).png']
    assert upload(client, '../outside.png').json['imported'][0]['filename'] == 'outside.png'
    assert (ctx.DATA_DIR.parent / 'outside.png').is_file()


def test_invalid_batch_is_atomic_and_does_not_publish_or_keep_files(samples):
    ctx, client = samples
    response = client.post('/api/samples/import', data={'files': [(picture(), 'valid.png'), (BytesIO(b'not an image'), 'bad.png')]})
    assert response.status_code == 400
    center = SampleCenter(ctx)
    assert 'valid.png' not in center.catalog()
    assert list(center.library.directory.iterdir()) == []
    assert client.post('/api/samples/import', data={}).status_code == 400
    assert upload(client, 'bad.html').status_code == 400
    assert upload(client, company='字' * 201).status_code == 400
    assert client.post('/api/samples/import', data={'files': [(picture(), f'{i}.png') for i in range(101)]}).status_code == 400


def test_imported_image_runs_and_deleted_history_stays_viewable(samples):
    ctx, client = samples
    seen = []
    def analyze(source, preview, **kwargs):
        seen.append(source)
        Image.open(source).save(preview)
        return {'fields': {}, 'overall': '需人工复核'}
    ctx._configured_analyze = analyze
    assert upload(client).status_code == 201
    assert client.post('/api/samples/run', json={'filenames': ['new.png']}).status_code == 202
    center = SampleCenter(ctx)
    job = center.store.claim()
    result, preview = center.worker.execute(job)
    center.store.finish(job, result, preview)
    assert seen[0].parent == center.library.directory
    assert result['filename'] == 'new.png'
    assert client.post('/api/samples/delete', json={'filenames': ['new.png']}).status_code == 200
    assert 'new.png' not in SampleCenter(ctx).catalog()
    assert client.get('/api/samples/image/new.png').status_code == 404
    assert client.get('/files/samples/original/new.png').status_code == 200
    history = client.get('/api/samples/history').json
    assert history[0]['filename'] == 'new.png'
    assert client.get(f"/api/samples/results/{history[0]['result_id']}").status_code == 200
    assert all(row['filename'] != 'new.png' for row in client.get('/api/samples/statistics').json['items'])
    assert client.post('/api/samples/run', json={'filenames': ['new.png']}).status_code == 400


def test_delete_is_atomic_blocks_running_and_removes_legacy_without_touching_source(samples):
    ctx, client = samples
    before = ctx.GROUND_TRUTH_PATH.read_bytes()
    client.post('/api/samples/run', json={'filenames': ['one.png']})
    assert client.post('/api/samples/delete', json={'filenames': ['one.png', 'two.png']}).status_code == 409
    assert 'two.png' in SampleCenter(ctx).catalog()
    assert client.post('/api/samples/delete', json={'filenames': ['two.png', 'missing.png']}).status_code == 409
    assert client.post('/api/samples/delete', json={'filenames': ['two.png', 'two.png']}).json['deleted'] == ['two.png']
    assert 'two.png' not in SampleCenter(ctx).catalog()
    assert (ctx.DATA_DIR / 'two.png').is_file()
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == before
    center = SampleCenter(ctx)
    # A stale run request cannot enqueue a sample deleted after its catalog read.
    with pytest.raises(LibraryConflict):
        center.store.enqueue(['two.png'], {}, {})
    assert client.get('/files/samples/original/two.png').status_code == 200
    assert client.get('/files/samples/original/../outside.png').status_code == 404


@pytest.mark.parametrize('payload', [{}, [], {'filenames': []}, {'filenames': [1]}, {'filenames': 'one.png'}])
def test_invalid_deletion_requests(samples, payload):
    _, client = samples
    assert client.post('/api/samples/delete', json=payload).status_code == 400
