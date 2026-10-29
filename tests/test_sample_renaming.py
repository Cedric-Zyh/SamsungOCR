"""Sample filenames follow recognized customer order numbers without losing evidence."""
import json

from PIL import Image
import pytest

from receipt_ocr.web.samples import SampleCenter
from test_sample_center import samples
from test_sample_library import upload


NUMBER = '7266220437'


def run_sample(ctx, client, name, fields=None, *, error=''):
    assert client.post('/api/samples/run', json={'filenames': [name]}).status_code == 202
    center = SampleCenter(ctx)
    job = center.store.claim()
    result = {'filename': name, 'fields': fields or {}, 'overall': '需人工复核'}
    assert center.store.finish(job, result, error=error)
    return center, job, center.store.get(job['id'])['result_id']


def test_recognition_renames_uploaded_sample_and_keeps_original_and_company(samples):
    ctx, client = samples
    assert upload(client, '扫描回单.png', '甲公司').status_code == 201
    before = SampleCenter(ctx)
    source = before.source('扫描回单.png')
    data = source.read_bytes()
    center, job, result_id = run_sample(ctx, client, '扫描回单.png', {'客户订单号': NUMBER})
    renamed = NUMBER + '.png'

    assert '扫描回单.png' not in center.catalog()
    assert center.catalog()[renamed]['fields']['客户名称'] == '甲公司'
    assert center.source(renamed) == source
    assert source.read_bytes() == data
    assert center.store.get(job['id'])['filename'] == renamed
    assert client.get('/api/samples/image/' + renamed).status_code == 200
    assert client.get('/api/samples/image/' + renamed + '?thumbnail=1').status_code == 200
    assert client.get('/files/samples/original/' + renamed).status_code == 200
    detail = client.get(f'/api/samples/results/{result_id}').json
    assert detail['filename'] == renamed
    assert client.get(detail['preview_url']).status_code == 200
    item = next(item for item in client.get('/api/samples').json['items'] if item['filename'] == renamed)
    assert item['status'] == '已完成'
    assert item['company'] == '甲公司'
    assert SampleCenter(ctx).source(renamed) == source
    assert ctx.database.list_results() == []


@pytest.mark.parametrize('name', [NUMBER + '.png', NUMBER + '_01.png', NUMBER + '_12.png',
                                  NUMBER + '-01.png', NUMBER + '-12.png'])
def test_already_numbered_sample_keeps_its_name_even_if_ocr_disagrees(samples, name):
    ctx, client = samples
    assert upload(client, name).status_code == 201
    center, _, result_id = run_sample(ctx, client, name, {'客户订单号': '7330650854'})
    assert name in center.catalog()
    assert center.database.get_result(result_id)['filename'] == name
    assert '7330650854.png' not in center.catalog()


@pytest.mark.parametrize('original,renamed', [
    ('scan_01.png', NUMBER + '_01.png'), ('scan-02.png', NUMBER + '-02.png'),
    ('SCAN.JPG', NUMBER + '.JPG'),
])
def test_rename_preserves_page_suffix_and_image_extension(samples, original, renamed):
    ctx, client = samples
    ctx.ALLOWED_EXTENSIONS.add('.jpg')
    assert upload(client, original).status_code == 201
    center, _, result_id = run_sample(ctx, client, original, {'客户订单号': NUMBER})
    assert renamed in center.catalog()
    assert center.database.get_result(result_id)['filename'] == renamed


@pytest.mark.parametrize('fields', [
    {}, {'客户订单号': ''}, {'客户订单号': '726622043'}, {'客户订单号': '72662204371'},
    {'客户订单号': '../7266220437'}, {'客户订单号': '7266220437/7330650854'},
    {'客户订单号': '７２６６２２０４３７'},
    {'销售订单号': NUMBER, '运单号': NUMBER},
])
def test_missing_or_invalid_customer_order_number_keeps_original_name(samples, fields):
    ctx, client = samples
    assert upload(client, 'scan.png').status_code == 201
    center, _, result_id = run_sample(ctx, client, 'scan.png', fields)
    assert 'scan.png' in center.catalog()
    assert center.database.get_result(result_id)['filename'] == 'scan.png'


@pytest.mark.parametrize('fields,expected', [({}, NUMBER), ({'客户订单号': '7330650854'}, '7330650854')])
def test_projected_internal_customer_order_number_is_available_for_renaming(samples, fields, expected):
    ctx, client = samples
    assert upload(client, 'scan.png').status_code == 201
    assert client.post('/api/samples/run', json={'filenames': ['scan.png']}).status_code == 202
    center = SampleCenter(ctx)
    job = center.store.claim()
    assert center.store.finish(job, {
        'fields': fields, 'internal_fields': {'客户订单号': NUMBER}, 'overall': '需人工复核',
    })
    result_id = center.store.get(job['id'])['result_id']
    assert expected + '.png' in center.catalog()
    assert center.database.get_result(result_id)['filename'] == expected + '.png'


def test_failed_recognition_does_not_rename_from_partial_result(samples):
    ctx, client = samples
    assert upload(client, 'scan.png').status_code == 201
    center, _, result_id = run_sample(ctx, client, 'scan.png', {'客户订单号': NUMBER}, error='识别中断')
    assert 'scan.png' in center.catalog()
    assert center.database.get_result(result_id)['filename'] == 'scan.png'
    assert client.get('/api/samples/history').json[0]['status'] == 'failed'


@pytest.mark.parametrize('occupied', [NUMBER + '.png', NUMBER + '.jpg', NUMBER + ' (2).jpg'])
def test_existing_number_across_extensions_or_legacy_copies_is_never_overwritten(samples, occupied):
    ctx, client = samples
    ctx.ALLOWED_EXTENSIONS.add('.jpg')
    assert upload(client, occupied, '原客户').status_code == 201
    assert upload(client, 'scan.png', '新客户').status_code == 201
    center = SampleCenter(ctx)
    source = center.source(occupied)
    before = source.read_bytes()
    center, _, result_id = run_sample(ctx, client, 'scan.png', {'客户订单号': NUMBER})
    assert center.source(occupied) == source
    assert source.read_bytes() == before
    assert center.catalog()[occupied]['fields']['客户名称'] == '原客户'
    assert center.catalog()['scan.png']['fields']['客户名称'] == '新客户'
    assert center.database.get_result(result_id)['filename'] == 'scan.png'


def test_builtin_number_is_reserved_when_renaming_uploaded_sample(samples):
    ctx, client = samples
    name = NUMBER + '.png'
    truth = json.loads(ctx.GROUND_TRUTH_PATH.read_text())
    truth[name] = {'fields': {'客户名称': '内置客户'}}
    ctx.GROUND_TRUTH_PATH.write_text(json.dumps(truth, ensure_ascii=False))
    Image.new('RGB', (32, 48), 'red').save(ctx.DATA_DIR / name)
    assert upload(client, 'scan.png').status_code == 201
    center, _, result_id = run_sample(ctx, client, 'scan.png', {'客户订单号': NUMBER})
    assert center.catalog()[name]['fields']['客户名称'] == '内置客户'
    assert center.source(name) == ctx.DATA_DIR / name
    assert center.database.get_result(result_id)['filename'] == 'scan.png'


def test_nonuploaded_builtin_sample_keeps_its_original_name(samples):
    ctx, client = samples
    before = ctx.GROUND_TRUTH_PATH.read_bytes()
    center, _, result_id = run_sample(ctx, client, 'one.png', {'客户订单号': NUMBER})
    assert center.source('one.png') == ctx.DATA_DIR / 'one.png'
    assert center.database.get_result(result_id)['filename'] == 'one.png'
    assert ctx.GROUND_TRUTH_PATH.read_bytes() == before


def test_historical_result_number_is_reserved_when_renaming(samples):
    ctx, client = samples
    assert upload(client, 'scan.png').status_code == 201
    center = SampleCenter(ctx)
    name = NUMBER + '.png'
    occupied_id = center.database.insert_result(filename=name, stored_name='old.png',
        preview_name='', task_id='', result={'fields': {}, 'overall': '通过'})
    center, _, result_id = run_sample(ctx, client, 'scan.png', {'客户订单号': NUMBER})
    assert center.database.get_result(occupied_id)['filename'] == name
    assert center.database.get_result(result_id)['filename'] == 'scan.png'


def test_renaming_preserves_annotations_history_statistics_reruns_and_deletion(samples):
    ctx, client = samples
    assert upload(client, 'scan.png', '甲公司').status_code == 201
    center, _, first_id = run_sample(ctx, client, 'scan.png')
    annotation_url = f'/api/samples/results/{first_id}/annotations'
    snapshot = client.get(f'/api/samples/results/{first_id}/ground-truth').json
    saved = client.patch(annotation_url, json={
        'revision': snapshot['revision'], 'human_note': '保留人工标注',
        'fields': {'签章要求': '甲公司专用章'}, 'seal_should_match': False,
    })
    assert saved.status_code == 200
    original = center.source('scan.png')
    center, _, second_id = run_sample(ctx, client, 'scan.png', {'客户订单号': NUMBER})
    name = NUMBER + '.png'
    assert center.catalog()[name] == saved.json['entry']
    assert {row['filename'] for row in client.get('/api/samples/history').json} == {name}
    assert center.database.get_result(first_id)['filename'] == name
    assert center.database.get_result(second_id)['filename'] == name
    truth = client.get(f'/api/samples/results/{first_id}/ground-truth').json
    assert truth['filename'] == name
    assert truth['entry']['human_note'] == '保留人工标注'
    assert len(truth['history']) == 1
    assert truth['history'][0]['filename'] == name
    stat = next(item for item in client.get('/api/samples/statistics').json['items'] if item['filename'] == name)
    assert stat['company'] == '甲公司'
    center, _, third_id = run_sample(ctx, client, name, {'客户订单号': '7330650854'})
    assert center.database.get_result(third_id)['filename'] == name
    assert client.post('/api/samples/delete', json={'filenames': [name]}).status_code == 200
    assert not original.exists()
    assert client.get('/api/samples/history').json == []
    assert client.get(f'/api/samples/results/{first_id}').status_code == 404


def test_worker_recognition_result_drives_renaming(samples):
    ctx, client = samples
    def analyze(source, preview, **kwargs):
        Image.open(source).save(preview)
        return {'fields': {'客户订单号': NUMBER}, 'overall': '需人工复核'}
    ctx._configured_analyze = analyze
    assert upload(client, 'scan.png').status_code == 201
    assert client.post('/api/samples/run', json={'filenames': ['scan.png']}).status_code == 202
    center = SampleCenter(ctx)
    job = center.store.claim()
    result, preview = center.worker.execute(job)
    assert center.store.finish(job, result, preview)
    result_id = center.store.get(job['id'])['result_id']
    detail = client.get(f'/api/samples/results/{result_id}').json
    assert detail['filename'] == NUMBER + '.png'
    assert client.get(detail['preview_url']).status_code == 200
    assert center.source(NUMBER + '.png').is_file()


def test_rename_and_result_commit_roll_back_together(samples, monkeypatch):
    ctx, client = samples
    assert upload(client, 'scan.png').status_code == 201
    assert client.post('/api/samples/run', json={'filenames': ['scan.png']}).status_code == 202
    center = SampleCenter(ctx)
    job = center.store.claim()
    original_insert = center.database.insert_result
    def fail_insert(**kwargs):
        raise RuntimeError('模拟保存失败')
    monkeypatch.setattr(center.database, 'insert_result', fail_insert)
    result = {'fields': {'客户订单号': NUMBER}, 'overall': '需人工复核'}
    with pytest.raises(RuntimeError, match='模拟保存失败'):
        center.store.finish(job, result)
    assert 'scan.png' in center.catalog()
    assert NUMBER + '.png' not in center.catalog()
    assert center.store.get(job['id'])['filename'] == 'scan.png'
    monkeypatch.setattr(center.database, 'insert_result', original_insert)
    assert center.store.finish(job, result)
    assert NUMBER + '.png' in center.catalog()
    assert center.store.finish(job, result) is False
