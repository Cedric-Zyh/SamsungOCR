"""Download returned seal images after text commits, on an independent worker.

The existing durable stamp state is the queue. Only applied tasks are eligible;
restarting resumes pending downloads without uploading or recognizing again.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading

from ..persistence.recognition_calls import trace_timestamp


IMAGE_FIELDS = ('returned_image_status', 'returned_image_error', 'returned_image_url',
                'body_url', 'color_isolated_oriented_url')


def patch_images(value, req_uuid, changes, *, pending_only=False):
    """Change image evidence only; keep all recognized and reviewed values."""
    count = 0
    if isinstance(value, dict):
        if (value.get('provider') == 'danzhengtong' and value.get('req_uuid') == req_uuid
                and (not pending_only or value.get('returned_image_status') == 'pending')):
            value.update(changes)
            count += 1
        for child in value.values():
            count += patch_images(child, req_uuid, changes, pending_only=pending_only)
    elif isinstance(value, list):
        for child in value:
            count += patch_images(child, req_uuid, changes, pending_only=pending_only)
    return count


class ReturnedImageWorker:
    def __init__(self, store, service, logger):
        self.store, self.service, self.logger = store, service, logger
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='danzhengtong-images', daemon=True)
        self._thread.start()

    def wake(self):
        self._wake.set()

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _next(self):
        with self.store.database.connect() as con:
            row = con.execute('''SELECT * FROM danzhengtong_jobs d
                WHERE active=1 AND status='applied' AND EXISTS (
                    SELECT 1 FROM json_each(d.seal_requests_json, '$.requests') child
                    WHERE json_extract(child.value, '$.image_download.status') IN ('queued','running'))
                ORDER BY created_at,id LIMIT 1''').fetchone()
            if row is None:
                return None
            task = dict(row)
            state = json.loads(task['seal_requests_json'])
            child = next(item for item in state['requests']
                         if (item.get('image_download') or {}).get('status') in {'queued', 'running'})
            result = con.execute("SELECT result_json FROM results WHERE id=? AND deleted_at=''",
                                 (task['result_id'],)).fetchone()
            payload = json.loads(result['result_json']) if result else {}
            # A newer recognition may already have replaced this stamp.
            present = patch_images(deepcopy(payload), child.get('req_uuid'), {}, pending_only=True)
            return task, child, bool(present)

    def _save(self, task, child, download, changes=None):
        from ..persistence.result_versions import result_revision
        with self.store.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT * FROM danzhengtong_jobs WHERE id=? AND active=1 AND status='applied'",
                              (task['id'],)).fetchone()
            if row is None:
                return False
            state = json.loads(row['seal_requests_json'])
            current = next((item for item in state.get('requests', [])
                            if item.get('req_uuid') == child.get('req_uuid')
                            and item.get('index') == child.get('index')), None)
            if current is None:
                return False
            result = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''",
                                 (row['result_id'],)).fetchone()
            eligible = True
            if download.get('status') == 'running' and (result is None or not patch_images(
                    json.loads(result['result_json']), child['req_uuid'], {}, pending_only=True)):
                download, eligible = {'status': 'stale'}, False
            if changes and result:
                payload = json.loads(result['result_json'])
                if patch_images(payload, child['req_uuid'], changes, pending_only=True):
                    original = json.loads(result['original_result_json'] or '{}')
                    patch_images(original, child['req_uuid'], changes, pending_only=True)
                    before_revision = result_revision(result)
                    con.execute('UPDATE results SET result_json=?,original_result_json=? WHERE id=?',
                                (json.dumps(payload, ensure_ascii=False), json.dumps(original, ensure_ascii=False), result['id']))
                    updated = con.execute('SELECT * FROM results WHERE id=?', (result['id'],)).fetchone()
                    # Image evidence is not a human edit. Preserve already
                    # queued retries' comparison revision if it still matches.
                    after_revision = result_revision(updated)
                    con.execute('''UPDATE recognition_jobs SET source_revision=?
                        WHERE COALESCE(result_id,target_result_id)=? AND source_revision=?''',
                                (after_revision, result['id'], before_revision))
                    con.execute('''UPDATE danzhengtong_jobs SET source_revision=?
                        WHERE result_id=? AND source_revision=?''',
                                (after_revision, result['id'], before_revision))
                else:
                    download = {**download, 'status': 'stale'}
            current['image_download'] = download
            transport = (current.get('call') or {}).get('transport')
            if isinstance(transport, dict) and download.get('started_at'):
                transport['image_download'] = download
            con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                        (json.dumps(state, ensure_ascii=False), row['id']))
            self.store.database.sync_remote_recognition_calls(con, row['job_id'])
            return eligible

    def step(self):
        item = self._next()
        if item is None:
            return False
        task, child, present = item
        if not present:
            self._save(task, child, {'status': 'stale'})
            return True
        from ..danzhengtong import Settings
        from ..danzhengtong_seal import _returned_artifact
        directory = Path(self.service.documents.artifact_dir) / ('dzt-' + task['id'])
        prefix = '/files/artifacts/dzt-' + task['id']
        enabled = Settings.load().download_seal_images
        download = {'status': 'running', 'started_at': trace_timestamp()} if enabled else {'status': 'disabled'}
        if enabled:
            if not self._save(task, child, download):
                return True
        artifact = _returned_artifact(child, child.get('result') or {}, directory, prefix)
        status = artifact['returned_image_status']
        download.update(status='completed' if status == 'downloaded' else 'disabled' if status == 'disabled' else 'failed')
        if enabled:
            download['finished_at'] = trace_timestamp()
        if artifact.get('returned_image_error'):
            download['error'] = artifact['returned_image_error']
        self._save(task, child, download, {key: artifact[key] for key in IMAGE_FIELDS})
        return True

    def _run(self):
        from .worker import ProcessLock
        lock = ProcessLock(self.store.database.path.with_suffix('.dzt.images.worker.lock'))
        try:
            while not self._stop.is_set():
                if lock.acquire():
                    break
                self._stop.wait(1)
            else:
                return
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    worked = self.step()
                except Exception:
                    worked = False
                    self.logger.exception('Danzhengtong image cache failed')
                self._wake.wait(.05 if worked else 1)
        finally:
            lock.release()
