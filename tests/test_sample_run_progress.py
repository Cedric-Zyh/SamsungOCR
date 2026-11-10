from sample_support import SampleCenter
from test_sample_center import samples


def test_run_progress_includes_remote_wait_and_is_scoped_to_original_run(samples):
    ctx, client = samples
    response = client.post('/api/samples/run', json={
        'filenames': ['one.png', 'two.png', 'three.png', 'blank.png'], 'mode': 'danzhengtong'})
    task_id = response.json['task_id']
    service = SampleCenter(ctx)
    initial = client.get(f'/api/samples/tasks/{task_id}').json
    assert initial['total'] == initial['submitting'] == 4
    assert initial['processed'] == 0 and not initial['finished']
    with service.database.connect() as con:
        for name, status, remote in [
            ('one.png', 'succeeded', 'waiting'), ('two.png', 'succeeded', 'applied'),
            ('three.png', 'failed', 'failed'), ('blank.png', 'queued', 'received'),
        ]:
            con.execute('UPDATE recognition_jobs SET status=? WHERE filename=?', (status, name))
            con.execute('UPDATE danzhengtong_jobs SET status=? WHERE job_id IN (SELECT id FROM recognition_jobs WHERE filename=?)', (remote, name))
    progress = client.get(f'/api/samples/tasks/{task_id}').json
    assert (progress['completed'], progress['failed'], progress['waiting'], progress['processed']) == (1, 1, 2, 2)
    assert not progress['finished']
    with service.database.connect() as con:
        con.execute("UPDATE recognition_jobs SET status='succeeded'")
        con.execute("UPDATE danzhengtong_jobs SET status='applied'")
    finished = client.get(f'/api/samples/tasks/{task_id}').json
    assert finished['processed'] == finished['total'] == 4 and finished['finished']
    newer = client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'local'}).json
    assert newer['task_id'] != task_id
    assert client.get(f'/api/samples/tasks/{task_id}').json == finished
    latest = client.get(f"/api/samples/tasks/{newer['task_id']}").json
    assert latest['total'] == latest['queued'] == 1 and latest['processed'] == 0


def test_progress_counts_uncertain_submissions_and_cancelled_jobs_as_attention(samples):
    ctx, client = samples
    response = client.post('/api/samples/run', json={'filenames': ['one.png', 'two.png'], 'mode': 'danzhengtong'})
    service = SampleCenter(ctx)
    with service.database.connect() as con:
        con.execute("UPDATE recognition_jobs SET status='succeeded' WHERE filename='one.png'")
        con.execute("UPDATE danzhengtong_jobs SET status='unknown' WHERE job_id IN (SELECT id FROM recognition_jobs WHERE filename='one.png')")
        con.execute("UPDATE recognition_jobs SET status='cancelled' WHERE filename='two.png'")
    progress = client.get(f"/api/samples/tasks/{response.json['task_id']}").json
    assert progress['failed'] == progress['cancelled'] == 1
    assert progress['processed'] == 2 and progress['finished']
    assert client.get('/api/samples/tasks/not-a-task').status_code == 404
    # Looking at progress never creates a recognition task or modifies the plan.
    assert len(client.get('/api/samples/history').json) == 2
