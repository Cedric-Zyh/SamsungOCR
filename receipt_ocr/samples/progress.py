"""Compact progress for one sample run, including its independent remote lane."""


def sample_run_progress(store, task_id):
    with store.database.connect() as con:
        if not con.execute('SELECT 1 FROM batch_tasks WHERE id=?', (task_id,)).fetchone():
            raise KeyError(task_id)
        rows = con.execute('''SELECT j.status,d.status AS remote_status FROM recognition_jobs j
            LEFT JOIN danzhengtong_jobs d ON d.job_id=j.id AND d.active=1 WHERE j.task_id=?''',
            (task_id,)).fetchall()
    counts = dict(total=len(rows), completed=0, failed=0, cancelled=0, waiting=0, submitting=0, running=0, queued=0)
    for row in rows:
        status, remote = row['status'], row['remote_status']
        if status == 'cancelled':
            counts['cancelled'] += 1
        elif remote in ('queued', 'submitting', 'waiting', 'received'):
            counts['waiting' if remote in ('waiting', 'received') else 'submitting'] += 1
        elif status in ('queued', 'running', 'awaiting_upload'):
            counts['running' if status == 'running' else 'queued'] += 1
        elif status == 'failed' or remote in ('failed', 'unknown', 'conflict'):
            counts['failed'] += 1
        else:
            counts['completed'] += 1
    processed = counts['completed'] + counts['failed'] + counts['cancelled']
    return {'task_id': task_id, **counts, 'processed': processed, 'finished': processed == counts['total']}
