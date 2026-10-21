from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

from flask import Flask
from PIL import Image
import pytest

from receipt_ocr.persistence.database import Database
from receipt_ocr.web.samples import SampleCenter, register_sample_routes
from receipt_ocr.web.bootstrap_data import export_package, import_package
from test_sample_center import samples


def target(tmp_path):
    root = tmp_path / 'target'
    root.mkdir()
    data = root / 'data'
    data.mkdir()
    truth = root / 'truth.json'
    truth.write_text('{}')
    db = Database(root / 'storage/results.db')
    db.initialize()
    app = Flask(__name__)
    app.config.update(TESTING=True, BACKGROUND_WORKER=False)
    ctx = SimpleNamespace(database=db, DATA_DIR=data, GROUND_TRUTH_PATH=truth,
                          ALLOWED_EXTENSIONS={'.png'}, app=app, _configured_analyze=lambda *a, **kw: {})
    register_sample_routes(app, ctx)
    return ctx, app.test_client()


def standard(ctx, requirement='甲公司公章', color='red'):
    directory = ctx.database.path.parent / 'seal-standards'
    directory.mkdir(exist_ok=True)
    name = 'original.png'
    Image.new('RGB', (40, 40), color).save(directory / name)
    return ctx.database.save_seal_standard({
        'requirement': requirement, 'image_name': name, 'recognized': requirement,
        'shape': 'round', 'color': color, 'source_result_id': 123,
        'source_filename': 'receipt.png', 'candidate_id': 'candidate', 'source_kind': 'sample',
    }, expected_revision=0)


def package(ctx, client):
    response = client.get('/api/bootstrap/export')
    assert response.status_code == 200
    data = response.data
    response.close()
    return data


def post(client, data):
    return client.post('/api/bootstrap/import', data={'file': (BytesIO(data), 'seed.zip')})


def rewrite(data, change):
    with ZipFile(BytesIO(data)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = json.loads(files['manifest.json'])
    change(manifest, files)
    files['manifest.json'] = json.dumps(manifest).encode()
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as z:
        for name, contents in files.items():
            z.writestr(name, contents)
    return output.getvalue()


def test_transfer_is_self_contained_and_repeatable(samples, tmp_path):
    source, source_client = samples
    standard(source)
    # Built-in dataset labels and imported samples must both travel.
    picture = BytesIO()
    Image.new('RGB', (20, 20), 'blue').save(picture, 'PNG')
    picture.seek(0)
    assert source_client.post('/api/samples/import', data={
        'company': '新增客户', 'files': (picture, 'new.png')}).status_code == 201
    source.database.set_retention_days(123)
    data = package(source, source_client)
    with ZipFile(BytesIO(data)) as z:
        manifest = json.loads(z.read('manifest.json'))
        assert len(manifest['samples']) == 5 and len(manifest['standards']) == 1
        assert not any(n.endswith('.db') for n in z.namelist())
        assert 'source_result_id' not in manifest['standards'][0]
    ctx, client = target(tmp_path)
    result = post(client, data)
    assert result.status_code == 200, result.json
    assert result.json == dict(samples_added=5, samples_skipped=0, standards_added=1, standards_skipped=0)
    service = SampleCenter(ctx)
    assert service.catalog()['one.png']['fields']['承运商'] == '承运乙'
    assert client.get('/api/samples/image/new.png').status_code == 200
    assert set(ctx.DATA_DIR.iterdir()) == set()
    saved = ctx.database.list_seal_standards()[0]
    assert saved['recognized'] == '甲公司公章'
    assert saved['source_kind'] == 'bootstrap' and saved['source_result_id'] == 0
    image = ctx.database.path.parent / 'seal-standards' / saved['image_url'].rsplit('/', 1)[-1]
    assert Image.open(image).getpixel((0, 0)) == (255, 0, 0)
    before = set(service.library.directory.iterdir())
    assert post(client, data).json == dict(samples_added=0, samples_skipped=5, standards_added=0, standards_skipped=1)
    assert set(service.library.directory.iterdir()) == before
    assert ctx.database.list_results() == [] and ctx.database.list_tasks() == []
    assert client.get('/api/samples/history').json == []
    assert ctx.database.get_retention_days() != 123
    # Imported installations can export the same seed data again.
    assert len(json.loads(ZipFile(BytesIO(package(ctx, client))).read('manifest.json'))['samples']) == 5


def test_local_standards_and_deleted_samples_are_preserved(samples, tmp_path):
    source, source_client = samples
    standard(source)
    data = package(source, source_client)
    ctx, client = target(tmp_path)
    existing = standard(ctx, color='blue')
    service = SampleCenter(ctx)
    with service.database.connect() as con:
        con.execute("INSERT INTO sample_library(filename,deleted_at) VALUES('one.png','deleted')")
    result = post(client, data)
    assert result.json == dict(samples_added=3, samples_skipped=1, standards_added=0, standards_skipped=1)
    assert ctx.database.list_seal_standards() == [existing]
    assert 'one.png' not in service.catalog()


@pytest.mark.parametrize('mutation', ['traversal', 'corrupt', 'duplicate', 'version', 'extra'])
def test_invalid_package_never_partially_imports(samples, tmp_path, mutation):
    source, client = samples
    standard(source)
    data = package(source, client)
    def change(manifest, files):
        if mutation == 'traversal':
            manifest['samples'][-1]['filename'] = '../escape.png'
        elif mutation == 'corrupt':
            files[manifest['standards'][0]['image']] = b'broken'
        elif mutation == 'duplicate':
            manifest['samples'].append(manifest['samples'][0])
        elif mutation == 'version':
            manifest['version'] = 999
        elif mutation == 'extra':
            files['../../outside.png'] = b'bad'
    ctx, destination = target(tmp_path)
    response = post(destination, rewrite(data, change))
    assert response.status_code == 400
    service = SampleCenter(ctx)
    assert service.catalog() == {} and ctx.database.list_seal_standards() == []
    assert list(service.library.directory.iterdir()) == []


def test_database_failure_rolls_back_both_databases_and_files(samples, tmp_path, monkeypatch):
    source, client = samples
    standard(source)
    data = package(source, client)
    ctx, _ = target(tmp_path)
    service = SampleCenter(ctx)
    def fail(*args, **kwargs):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(ctx.database, 'save_seal_standard', fail)
    with pytest.raises(OSError):
        import_package(ctx, service, BytesIO(data))
    assert service.catalog() == {}
    assert list(service.library.directory.iterdir()) == []
    assert list((ctx.database.path.parent / 'seal-standards').iterdir()) == []
    assert ctx.database.list_seal_standards() == []


def test_missing_and_non_zip_uploads_are_reported(samples):
    _, client = samples
    assert client.post('/api/bootstrap/import').status_code == 400
    assert post(client, b'not a zip').status_code == 400


@pytest.mark.parametrize('scope,expected', [('samples', (4, 0)), ('standards', (0, 1))])
def test_export_and_import_independent_packages(samples, tmp_path, scope, expected):
    source, client = samples
    standard(source)
    response = client.get('/api/bootstrap/export', query_string={'scope': scope})
    assert response.status_code == 200
    assert f'samsung-receipt-{scope}.zip' in response.headers['Content-Disposition']
    data = response.data
    response.close()
    with ZipFile(BytesIO(data)) as z:
        manifest = json.loads(z.read('manifest.json'))
        assert (len(manifest['samples']), len(manifest['standards'])) == expected
        assert all(n == 'manifest.json' or n.startswith(scope + '/') for n in z.namelist())
    ctx, destination = target(tmp_path)
    assert post(destination, data).status_code == 200
    assert len(SampleCenter(ctx).catalog()) == expected[0]
    assert len(ctx.database.list_seal_standards()) == expected[1]


@pytest.mark.parametrize('scope,expected', [('samples', (4, 0)), ('standards', (0, 1))])
def test_legacy_combined_package_can_import_only_one_kind(samples, tmp_path, scope, expected):
    source, client = samples
    standard(source)
    data = package(source, client)
    ctx, destination = target(tmp_path)
    response = destination.post('/api/bootstrap/import', data={
        'file': (BytesIO(data), 'combined.zip'), 'scope': scope})
    assert response.status_code == 200
    assert response.json['samples_added'] == expected[0]
    assert response.json['standards_added'] == expected[1]
    assert len(SampleCenter(ctx).catalog()) == expected[0]
    assert len(ctx.database.list_seal_standards()) == expected[1]
    # Importing the other component later still works without duplicates.
    response = post(destination, data)
    assert response.json['samples_added'] == 4 - expected[0]
    assert response.json['standards_added'] == 1 - expected[1]


def test_invalid_scope_changes_nothing(samples):
    ctx, client = samples
    assert client.get('/api/bootstrap/export?scope=unknown').status_code == 400
    response = client.post('/api/bootstrap/import', data={
        'file': (BytesIO(b'ignored'), 'seed.zip'), 'scope': 'unknown'})
    assert response.status_code == 400
    assert ctx.database.list_seal_standards() == []
