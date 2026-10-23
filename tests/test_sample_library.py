from io import BytesIO
from pathlib import Path

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


def test_import_names_never_overwrite_existing_but_deleted_numbers_are_released(samples):
    ctx, client = samples
    duplicate = upload(client, 'one.png', '').json
    assert duplicate['imported'] == []
    assert duplicate['skipped'] == [{'filename': 'one.png', 'number': 'one'}]
    first = upload(client, 'new.png', '').json['imported'][0]['filename']
    assert first == 'new.png'
    assert client.get('/api/samples').json['items'][-1]['company'] == '未分类'
    assert client.post('/api/samples/delete', json={'filenames': [first]}).status_code == 200
    assert upload(client, 'new.png').json['imported'][0]['filename'] == 'new.png'
    response = client.post('/api/samples/import', data={'files': [(picture(), 'duplicate.png'), (picture(), 'duplicate.png')]})
    assert [row['filename'] for row in response.json['imported']] == ['duplicate.png']
    assert len(response.json['skipped']) == 1
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


def test_import_deduplicates_numbers_across_extensions_companies_and_legacy_copies(samples):
    ctx, client = samples
    ctx.ALLOWED_EXTENSIONS.update({'.jpg', '.jpeg'})
    first = upload(client, '7329228596.jpg', '甲公司')
    assert len(first.json['imported']) == 1
    center = SampleCenter(ctx)
    before = set(center.library.directory.iterdir())
    for name in ['7329228596.png', '7329228596.JPG', '7329228596 (2).jpeg']:
        response = upload(client, name, '乙公司')
        assert response.status_code == 201
        assert response.json['imported'] == []
        assert response.json['skipped'][0]['number'] == '7329228596'
    assert set(center.library.directory.iterdir()) == before
    assert center.catalog()['7329228596.jpg']['fields']['客户名称'] == '甲公司'
    response = client.post('/api/samples/import', data={'files': [
        (picture(), '7330650854.png'), (picture(), '7330650854.jpg'), (picture(), '7330650855.png')]})
    assert [row['filename'] for row in response.json['imported']] == ['7330650854.png', '7330650855.png']
    assert len(response.json['skipped']) == 1


def test_deleting_sample_removes_original_results_and_history_then_allows_new_import(samples):
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
    original = center.source('new.png')
    result_id = client.get('/api/samples/history').json[0]['result_id']
    assert seen[0].parent == center.library.directory
    assert result['filename'] == 'new.png'
    assert client.post('/api/samples/delete', json={'filenames': ['new.png']}).status_code == 200
    assert 'new.png' not in SampleCenter(ctx).catalog()
    assert client.get('/api/samples/image/new.png').status_code == 404
    assert client.get('/files/samples/original/new.png').status_code == 404
    history = client.get('/api/samples/history').json
    assert history == []
    assert not original.exists()
    assert not (center.preview_dir / preview).exists()
    assert not (center.artifact_dir / Path(preview).stem).exists()
    assert client.get(f"/api/samples/results/{result_id}").status_code == 404
    assert all(row['filename'] != 'new.png' for row in client.get('/api/samples/statistics').json['items'])
    assert client.post('/api/samples/run', json={'filenames': ['new.png']}).status_code == 400
    assert upload(client, 'new.png', '').json['imported'][0]['filename'] == 'new.png'
    imported = next(r for r in client.get('/api/samples').json['items'] if r['filename'] == 'new.png')
    assert imported['company'] == '未分类'
    assert imported['status'] == '未测试'
    assert ctx.database.list_results() == []


def test_delete_is_atomic_blocks_running_and_removes_legacy_source(samples):
    ctx, client = samples
    before = ctx.GROUND_TRUTH_PATH.read_bytes()
    client.post('/api/samples/run', json={'filenames': ['one.png']})
    assert client.post('/api/samples/delete', json={'filenames': ['one.png', 'two.png']}).status_code == 409
    assert 'two.png' in SampleCenter(ctx).catalog()
    assert client.post('/api/samples/delete', json={'filenames': ['two.png', 'missing.png']}).status_code == 409
    assert client.post('/api/samples/delete', json={'filenames': ['two.png', 'two.png']}).json['deleted'] == ['two.png']
    assert 'two.png' not in SampleCenter(ctx).catalog()
    assert not (ctx.DATA_DIR / 'two.png').exists()
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == before
    center = SampleCenter(ctx)
    # A stale run request cannot enqueue a sample deleted after its catalog read.
    with pytest.raises(LibraryConflict):
        center.store.enqueue(['two.png'], {}, {})
    assert client.get('/files/samples/original/two.png').status_code == 404
    assert client.get('/files/samples/original/../outside.png').status_code == 404


def test_delete_rolls_back_files_and_database_if_file_move_fails(samples, monkeypatch):
    from receipt_ocr.web import sample_library
    ctx, client = samples
    upload(client, 'first.png', '')
    upload(client, 'second.png', '')
    center = SampleCenter(ctx)
    originals = [center.source(name) for name in ['first.png', 'second.png']]
    move = sample_library.shutil.move
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('test file failure')
        return move(source, destination)

    monkeypatch.setattr(sample_library.shutil, 'move', fail_once)
    with pytest.raises(OSError):
        center.library.remove(['first.png', 'second.png'])
    assert all(p.is_file() for p in originals)
    assert all(name in center.catalog() for name in ['first.png', 'second.png'])


def test_old_soft_deleted_sample_can_be_purged_and_its_number_reused(samples):
    ctx, client = samples
    upload(client, '7330697792.png', '旧公司')
    center = SampleCenter(ctx)
    original = center.source('7330697792.png')
    with center.database.connect() as con:
        con.execute("UPDATE sample_library SET deleted_at='2026-10-22' WHERE filename='7330697792.png'")
    assert center.library.remove(['7330697792.png'])['deleted'] == ['7330697792.png']
    assert not original.exists()
    assert upload(client, '7330697792.png', '').json['imported'][0]['filename'] == '7330697792.png'
    assert center.catalog()['7330697792.png']['fields']['客户名称'] == ''


def test_deleting_one_sample_keeps_the_other_batch_result_and_production(samples):
    ctx, client = samples
    production = ctx.database.insert_result(filename='one.png', stored_name='production.png',
        preview_name='', task_id='', result={'fields': {}, 'overall': '通过'})
    client.post('/api/samples/run', json={'filenames': ['one.png', 'two.png']})
    center = SampleCenter(ctx)
    for _ in range(2):
        job = center.store.claim()
        result, preview = center.worker.execute(job)
        center.store.finish(job, result, preview)
    before = {r['filename']: r['result_id'] for r in client.get('/api/samples/history').json}
    assert center.library.remove(['one.png'])['deleted'] == ['one.png']
    assert client.get(f"/api/samples/results/{before['one.png']}").status_code == 404
    assert client.get(f"/api/samples/results/{before['two.png']}").status_code == 200
    assert ctx.database.get_result(production)['filename'] == 'one.png'
    with center.database.connect() as con:
        assert con.execute('SELECT total FROM batch_tasks').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM recognition_jobs').fetchone()[0] == 1


@pytest.mark.parametrize('payload', [{}, [], {'filenames': []}, {'filenames': [1]}, {'filenames': 'one.png'}])
def test_invalid_deletion_requests(samples, payload):
    _, client = samples
    assert client.post('/api/samples/delete', json=payload).status_code == 400


def finish_with_customer(ctx, client, name, company):
    assert client.post('/api/samples/run', json={'filenames': [name]}).status_code == 202
    center = SampleCenter(ctx)
    center.store.finish(center.store.claim(), {'fields': {'客户名称': company}, 'overall': '通过'})


def test_classify_merges_and_creates_companies_persistently_without_changing_sources(samples):
    ctx, client = samples
    original_truth = ctx.GROUND_TRUTH_PATH.read_bytes()
    assert upload(client, 'imported.png', '').status_code == 201
    finish_with_customer(ctx, client, 'blank.png', '  甲公司  ')
    finish_with_customer(ctx, client, 'imported.png', '新识别公司')
    # Already assigned samples retain their original customer even if OCR disagrees.
    finish_with_customer(ctx, client, 'one.png', '另一个公司')
    response = client.post('/api/samples/classify')
    assert response.status_code == 200
    assert response.json['classified'] == [
        {'filename': 'blank.png', 'company': '甲公司', 'requirement': ''},
        {'filename': 'imported.png', 'company': '新识别公司', 'requirement': ''}]
    assert response.json['created_companies'] == ['新识别公司']
    assert response.json['remaining'] == 2
    restarted = SampleCenter(ctx)
    items = {row['filename']: row for row in restarted.listing()['items']}
    assert items['blank.png']['company'] == '甲公司'
    assert items['imported.png']['company'] == '新识别公司'
    assert items['one.png']['company'] == '甲公司'
    assert restarted.source('blank.png') == ctx.DATA_DIR / 'blank.png'
    assert restarted.source('imported.png').parent == restarted.library.directory
    assert client.get('/api/samples/image/imported.png').status_code == 200
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == original_truth
    assert client.post('/api/samples/classify').json['classified'] == []
    assert ctx.database.list_results() == []


def test_classify_skips_unread_busy_and_deleted_samples(samples):
    ctx, client = samples
    assert client.post('/api/samples/classify').json['remaining'] == 1
    finish_with_customer(ctx, client, 'blank.png', '')
    assert client.post('/api/samples/classify').json['classified'] == []
    finish_with_customer(ctx, client, 'blank.png', '新公司')
    client.post('/api/samples/run', json={'filenames': ['blank.png']})
    assert client.post('/api/samples/classify').json['classified'] == []
    center = SampleCenter(ctx)
    center.store.finish(center.store.claim(), error='重试失败')
    # Classification only uses the latest completed result, not an older success.
    assert client.post('/api/samples/classify').json['classified'] == []
    upload(client, 'deleted.png', '')
    finish_with_customer(ctx, client, 'deleted.png', '已删除公司')
    client.post('/api/samples/delete', json={'filenames': ['deleted.png']})
    assert client.post('/api/samples/classify').json['created_companies'] == []
