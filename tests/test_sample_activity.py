"""Deletion and admission respect every durable background continuation."""
import json
import uuid

import pytest

from receipt_ocr.jobs.activity import active_job_filenames
from sample_support import SampleCenter
from test_sample_center import samples


def remote_sample(ctx, client, *, status='waiting', download=None, active=1):
    response = client.post('/api/samples/run', json={'filenames': ['one.png']})
    assert response.status_code == 202
    center = SampleCenter(ctx)
    job = center.store.claim()
    center.store.finish(job, {'fields': {}, 'overall': '需人工复核'})
    remote_id = uuid.uuid4().hex
    state = {'requests': [{'image_download': {'status': download}}]} if download else {'requests': []}
    with center.database.connect() as con:
        con.execute('''INSERT INTO danzhengtong_jobs
            (id,job_id,active,status,created_at,updated_at,req_uuid,seal_requests_json)
            VALUES(?,?,?,?,?,?,?,?)''',
            (remote_id, job['id'], active, status, job['created_at'], job['created_at'],
             'already-accepted', json.dumps(state)))
    artifacts = center.artifact_dir / ('dzt-' + remote_id)
    artifacts.mkdir()
    (artifacts / 'evidence.png').write_bytes((ctx.DATA_DIR / 'one.png').read_bytes())
    return center, job, remote_id, artifacts


@pytest.mark.parametrize('status,download', [
    ('queued', None), ('submitting', None), ('waiting', None), ('received', None),
    ('applied', 'queued'), ('applied', 'running'),
])
def test_remote_continuation_blocks_stale_delete_atomically(samples, status, download):
    ctx, client = samples
    center, job, remote_id, artifacts = remote_sample(ctx, client, status=status, download=download)
    response = client.post('/api/samples/delete', json={'filenames': ['one.png', 'two.png']})
    assert response.status_code == 409
    assert (ctx.DATA_DIR / 'one.png').is_file()
    assert (ctx.DATA_DIR / 'two.png').is_file()
    assert (artifacts / 'evidence.png').is_file()
    with center.database.connect() as con:
        assert con.execute('SELECT req_uuid FROM danzhengtong_jobs WHERE id=?', (remote_id,)).fetchone()[0] == 'already-accepted'
        assert con.execute('SELECT status FROM recognition_jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'succeeded'


@pytest.mark.parametrize('download', ['queued', 'running'])
def test_pending_images_also_block_duplicate_sample_run(samples, download):
    ctx, client = samples
    center, _, _, _ = remote_sample(ctx, client, status='applied', download=download)
    response = client.post('/api/samples/run', json={'filenames': ['one.png', 'two.png']})
    assert response.status_code == 202
    assert response.json['accepted'] == ['two.png']
    assert response.json['skipped'] == ['one.png']


@pytest.mark.parametrize('status,download,active', [
    ('applied', 'completed', 1), ('applied', 'failed', 1), ('failed', None, 1),
    ('waiting', None, 0),
])
def test_finished_sample_removes_remote_artifacts_but_keeps_other_files(samples, status, download, active):
    ctx, client = samples
    center, _, remote_id, artifacts = remote_sample(ctx, client, status=status, download=download, active=active)
    unrelated = center.artifact_dir / 'dzt-other'
    unrelated.mkdir()
    response = client.post('/api/samples/delete', json={'filenames': ['one.png']})
    assert response.status_code == 200
    assert not artifacts.exists()
    assert not (ctx.DATA_DIR / 'one.png').exists()
    assert unrelated.is_dir()
    assert (ctx.DATA_DIR / 'two.png').is_file()
    with center.database.connect() as con:
        assert con.execute('SELECT 1 FROM danzhengtong_jobs WHERE id=?', (remote_id,)).fetchone() is None


def test_activity_scope_never_broadens_an_empty_selection(samples):
    ctx, client = samples
    center, _, _, _ = remote_sample(ctx, client)
    with center.database.connect() as con:
        assert active_job_filenames(con, []) == set()
        assert active_job_filenames(con, ['two.png']) == set()
        assert active_job_filenames(con, ['one.png'] * 401) == {'one.png'}
        assert active_job_filenames(con) == {'one.png'}
