"""Durable per-image queue. Result writes and job completion share one transaction."""
from copy import deepcopy
import hashlib
import json
import uuid

from .database import now_iso
from .recognition_progress import public_progress


ACTIVE = ('awaiting_upload', 'ready', 'queued', 'running')
PUBLIC_STATUS_SQL = "CASE WHEN status='queued' AND start_requested=0 THEN 'ready' ELSE status END"
PROGRESS_STAGE_LABELS = {
    'uploading': '正在上传到识别服务',
    'submitting': '正在提交识别',
    'waiting': '等待识别结果',
    'paused': '识别查询已暂停',
    'completed': '识别服务已返回结果',
    'failed': '识别服务返回失败',
}


def result_revision(row, *, legacy=False):
    values = [row[key] for key in ('result_json', 'review_status', 'final_result',
                                   'human_note', 'attempt', 'deleted_at')]
    if legacy:
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
    payload = json.loads(values[0])
    # Page associations are a projection of the current batch, not a human
    # edit. Linking another page must not invalidate an otherwise safe retry.
    for key in ('page_group', 'page_group_id', 'page_role', 'parent_result_id', 'page_index'):
        payload.pop(key, None)
    values[0] = payload
    return 'v2:' + hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class JobStore:
    def __init__(self, database):
        self.database = database

    def initialize(self):
        with self.database.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS recognition_jobs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    client_key TEXT NOT NULL, filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'awaiting_upload'
                        CHECK(status IN ('awaiting_upload','queued','running','succeeded','failed','cancelled')),
                    start_requested INTEGER NOT NULL DEFAULT 1,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, uploaded_at TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT '',
                    result_id INTEGER, target_result_id INTEGER,
                    source_revision TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    UNIQUE(task_id, client_key),
                    FOREIGN KEY(task_id) REFERENCES batch_tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_state ON recognition_jobs(status,created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_task ON recognition_jobs(task_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_retry
                    ON recognition_jobs(target_result_id)
                    WHERE target_result_id IS NOT NULL AND status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS recognition_job_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(job_id) REFERENCES recognition_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_job_history_job ON recognition_job_history(job_id,id);
                CREATE INDEX IF NOT EXISTS idx_job_history_result_lookup ON recognition_jobs(result_id,target_result_id);
                CREATE TABLE IF NOT EXISTS queue_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    paused INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO queue_control(id,paused,updated_at) VALUES(1,0,'');
                CREATE TRIGGER IF NOT EXISTS pause_recognition_claim
                    BEFORE UPDATE OF status ON recognition_jobs
                    WHEN NEW.status='running' AND OLD.status!='running'
                         AND EXISTS(SELECT 1 FROM queue_control WHERE id=1 AND paused=1)
                    BEGIN SELECT RAISE(ABORT,'recognition_queue_paused'); END;
            ''')
            con.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(recognition_jobs)')}
            if 'start_requested' not in columns:
                # Jobs imported before this change already had permission to run.
                con.execute('ALTER TABLE recognition_jobs ADD COLUMN start_requested INTEGER NOT NULL DEFAULT 1')
            if 'progress_json' not in columns:
                con.execute("ALTER TABLE recognition_jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'")
            con.execute('''CREATE TRIGGER IF NOT EXISTS require_recognition_start
                BEFORE UPDATE OF status ON recognition_jobs
                WHEN NEW.status='running' AND OLD.status!='running' AND NEW.start_requested=0
                BEGIN SELECT RAISE(ABORT,'recognition_start_required'); END''')
            con.execute('CREATE INDEX IF NOT EXISTS idx_jobs_start ON recognition_jobs(status,start_requested,uploaded_at)')
            # Upgrade only revisions that still match their original snapshot.
            # A genuinely edited legacy target must retain its conflict marker.
            legacy_jobs = con.execute('''SELECT j.id AS retry_job_id,j.source_revision,r.*
                FROM recognition_jobs j JOIN results r ON r.id=j.target_result_id
                WHERE j.status IN ('queued','running') AND j.source_revision NOT LIKE 'v2:%'
            ''').fetchall()
            for row in legacy_jobs:
                if result_revision(row, legacy=True) == row['source_revision']:
                    con.execute('UPDATE recognition_jobs SET source_revision=? WHERE id=?',
                                (result_revision(row), row['retry_job_id']))

    @staticmethod
    def _log(con, job_id, task_id, event_type, title, detail='', status='', metadata=None, event_at=None):
        con.execute('''INSERT INTO recognition_job_history
            (job_id,task_id,event_at,event_type,title,detail,status,metadata_json)
            VALUES(?,?,?,?,?,?,?,?)''',
            (job_id, task_id, event_at or now_iso(), event_type, title, detail, status,
             json.dumps(metadata or {}, ensure_ascii=False)))

    @staticmethod
    def _progress_title(event):
        return PROGRESS_STAGE_LABELS.get(event.get('stage'), '识别进度更新')

    @staticmethod
    def _progress_detail(event):
        parts = []
        if event.get('provider') == 'danzhengtong':
            parts.append('单证通')
        elif event.get('provider'):
            parts.append(str(event['provider']))
        if event.get('poll_count') is not None:
            parts.append(f"查询 {event['poll_count']} 次")
        if event.get('timeout_seconds'):
            parts.append(f"最长等待 {event['timeout_seconds']} 秒")
        if event.get('message'):
            parts.append(str(event['message']))
        return ' · '.join(parts)

    def create_batch(self, task_id, name, items, options):
        timestamp = now_iso()
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('''INSERT INTO batch_tasks
                (id,name,created_at,updated_at,status,total,ocr_backend,seal_recognition_mode,recognition_config,execution_mode)
                VALUES(?,?,?,?,?,?,?,?,?,'queue')''',
                (task_id, name, timestamp, timestamp, '等待上传', len(items),
                 options['ocr_backend'], options['seal_recognition_mode'], json.dumps(options.get('recognition_config'))))
            for index, item in enumerate(items):
                job_id = uuid.uuid4().hex
                con.execute('''INSERT INTO recognition_jobs
                    (id,task_id,client_key,filename,payload_json,created_at,start_requested)
                    VALUES(?,?,?,?,?,?,0)''',
                    (job_id, task_id, str(index), item['filename'], json.dumps(options, ensure_ascii=False), timestamp))
                self._log(con, job_id, task_id, 'submitted', '已提交文件', item['filename'],
                          'awaiting_upload', event_at=timestamp)
        return self.task(task_id)

    def get(self, job_id):
        with self.database.connect() as con:
            row = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    @staticmethod
    def public(job):
        output = {key: job[key] for key in (
            'id', 'task_id', 'client_key', 'filename', 'status', 'created_at',
            'uploaded_at', 'started_at', 'finished_at', 'result_id',
            'target_result_id', 'attempt', 'error_message')}
        output['start_requested'] = bool(job['start_requested'])
        output['progress'] = public_progress(job)
        if output['status'] == 'queued' and not output['start_requested']:
            output['status'] = 'ready'
        options = json.loads(job['payload_json'])
        plan = options.get('recognition_config')
        output['uses_remote'] = any(method in {'qingtong', 'danzhengtong'} for methods in plan.values() for method in methods) if plan is not None else options.get('seal_recognition_mode') in ('qingtong', 'qingtong_only')
        output['review_status'] = job.get('review_status', '')
        output['final_result'] = job.get('final_result', '')
        return output

    def accept_upload(self, job_id, stored_name):
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            accepted = row['status'] == 'awaiting_upload'
            if accepted:
                timestamp = now_iso()
                con.execute("UPDATE recognition_jobs SET stored_name=?,status='queued',uploaded_at=?,error_message='' WHERE id=?",
                            (stored_name, timestamp, job_id))
                self._log(con, job_id, row['task_id'], 'uploaded', '文件已上传',
                          '已保存到本地，等待开始识别', 'ready', event_at=timestamp)
                self._refresh(con, row['task_id'])
        return self.get(job_id), accepted

    def start_jobs(self, job_ids):
        """Authorize only the selected, locally uploaded jobs; repeated starts are harmless."""
        if (not isinstance(job_ids, list) or not job_ids or len(job_ids) > 5000
                or any(not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 100
                       for job_id in job_ids)):
            raise ValueError('请选择有效的待开始回单')
        job_ids = list(dict.fromkeys(job_ids))
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            jobs = []
            for job_id in job_ids:
                row = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
                if row is None:
                    raise KeyError(job_id)
                if row['status'] == 'awaiting_upload' or not row['stored_name']:
                    raise ValueError('所选回单尚未导入完成，请完成导入后再开始')
                if not row['start_requested'] and row['status'] != 'queued':
                    raise ValueError('所选回单当前不能开始识别')
                jobs.append(row)
            started = 0
            tasks = set()
            for job in jobs:
                if not job['start_requested']:
                    con.execute('UPDATE recognition_jobs SET start_requested=1 WHERE id=?', (job['id'],))
                    self._log(con, job['id'], job['task_id'], 'queued', '已加入识别队列',
                              '等待后台处理', 'queued')
                    started += 1
                    tasks.add(job['task_id'])
            for task_id in tasks:
                self._refresh(con, task_id)
            items = [self.public(dict(con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()))
                     for job_id in job_ids]
        return {'started': started, 'items': items}

    def create_retries(self, task_id, result_ids, options):
        """All selections enqueue atomically; duplicate active retries are rejected."""
        timestamp = now_iso()
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = []
            for result_id in result_ids:
                row = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
                if row is None:
                    raise KeyError(result_id)
                active = con.execute("SELECT id FROM recognition_jobs WHERE target_result_id=? AND status IN ('queued','running')", (result_id,)).fetchone()
                if active is not None:
                    raise ValueError('所选回单已在等待识别或识别中，请等待该次任务完成')
                rows.append(row)
            con.execute('''INSERT INTO batch_tasks
                (id,name,created_at,updated_at,status,total,ocr_backend,seal_recognition_mode,recognition_config,execution_mode)
                VALUES(?,?,?,?,?,?,?,?,?,'queue')''',
                (task_id, '重新识别', timestamp, timestamp, '等待识别', len(rows), '', 'local', 'null'))
            for index, row in enumerate(rows):
                payload = deepcopy(options[row['id']])
                payload['previous_fields'] = json.loads(row['result_json']).get('fields', {})
                stored_name = payload.pop('_stored_name', row['stored_name'])
                job_id = uuid.uuid4().hex
                con.execute('''INSERT INTO recognition_jobs
                    (id,task_id,client_key,filename,stored_name,status,payload_json,created_at,uploaded_at,target_result_id,source_revision)
                    VALUES(?,?,?,?,?,'queued',?,?,?,?,?)''',
                    (job_id, task_id, str(index), row['filename'], stored_name,
                     json.dumps(payload, ensure_ascii=False), timestamp, timestamp, row['id'], result_revision(row)))
                self._log(con, job_id, task_id, 'retry_submitted', '重新识别已提交',
                          f"基于记录 #{row['id']} 重新识别", 'queued', {'result_id': row['id']},
                          event_at=timestamp)
        return self.task(task_id)

    def retry_failed(self, job_id):
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job['status'] in ('queued', 'running'):
                return dict(job)
            if job['status'] != 'failed':
                raise ValueError('只有失败项可以在此重试')
            target = job['target_result_id'] or job['result_id']
            revision = ''
            payload = json.loads(job['payload_json'])
            if target:
                row = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (target,)).fetchone()
                if row is None:
                    raise ValueError('原记录已删除，不能重试')
                if con.execute("SELECT 1 FROM recognition_jobs WHERE target_result_id=? AND status IN ('queued','running') AND id!=?", (target, job_id)).fetchone():
                    raise ValueError('该回单已有重试任务')
                revision = result_revision(row)
                payload['previous_fields'] = json.loads(row['result_json']).get('fields', {})
            con.execute("""UPDATE recognition_jobs SET status='queued',start_requested=1,target_result_id=?,source_revision=?,
                payload_json=?,started_at='',finished_at='',error_message='',progress_json='{}' WHERE id=?""",
                (target, revision, json.dumps(payload, ensure_ascii=False), job_id))
            self._log(con, job_id, job['task_id'], 'retry_queued', '已重新提交识别',
                      '失败项已回到后台队列', 'queued')
            self._refresh(con, job['task_id'])
        return self.get(job_id)

    def delete_job(self, job_id):
        """Delete an unfinished queue item so it can never be claimed again."""
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job['status'] not in ACTIVE:
                return dict(job), False
            task_id = job['task_id']
            stored_name = job['stored_name']
            preserve_upload = bool(job['target_result_id'] or job['result_id'])
            con.execute('DELETE FROM recognition_jobs WHERE id=?', (job_id,))
            con.execute('UPDATE batch_tasks SET total=MAX(0,total-1) WHERE id=?', (task_id,))
            self._refresh(con, job['task_id'])
        deleted = dict(job)
        deleted['preserve_upload'] = preserve_upload
        deleted['stored_name'] = stored_name
        return deleted, True

    def cancel(self, job_id):
        """Compatibility alias for callers that use the queue action name."""
        return self.delete_job(job_id)

    def delete_jobs(self, job_ids):
        """Delete unfinished queue items atomically; finished rows are reported but kept."""
        if (not isinstance(job_ids, list) or not job_ids or len(job_ids) > 5000
                or any(not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 100
                       for job_id in job_ids)):
            raise ValueError('请选择有效的待处理回单')
        job_ids = list(dict.fromkeys(job_ids))
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = []
            for job_id in job_ids:
                row = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
                if row is None:
                    raise KeyError(job_id)
                rows.append(row)
            deleted, kept, tasks = [], [], set()
            for row in rows:
                if row['status'] not in ACTIVE:
                    kept.append(dict(row))
                    continue
                con.execute('DELETE FROM recognition_jobs WHERE id=?', (row['id'],))
                con.execute('UPDATE batch_tasks SET total=MAX(0,total-1) WHERE id=?', (row['task_id'],))
                item = dict(row)
                item['preserve_upload'] = bool(row['target_result_id'] or row['result_id'])
                deleted.append(item)
                tasks.add(row['task_id'])
            for task_id in tasks:
                self._refresh(con, task_id)
        return {'deleted': deleted, 'kept': kept}

    def recover(self):
        """Called only while holding the worker's exclusive process lock."""
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            running = con.execute("SELECT id,task_id FROM recognition_jobs WHERE status='running'").fetchall()
            tasks = {row['task_id'] for row in running}
            con.execute("UPDATE recognition_jobs SET status='queued',started_at='',progress_json='{}',error_message='服务重启后继续识别' WHERE status='running'")
            for row in running:
                self._log(con, row['id'], row['task_id'], 'recovered', '服务重启后继续排队',
                          '上次处理中断，已交给后台继续处理', 'queued')
            for task_id in tasks:
                self._refresh(con, task_id)

    def claim(self):
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT paused FROM queue_control WHERE id=1').fetchone()[0]:
                return None
            row = con.execute("SELECT * FROM recognition_jobs WHERE status='queued' AND start_requested=1 ORDER BY uploaded_at,rowid LIMIT 1").fetchone()
            if row is None:
                return None
            timestamp = now_iso()
            con.execute("UPDATE recognition_jobs SET status='running',started_at=?,attempt=attempt+1,error_message='',progress_json='{}' WHERE id=?",
                        (timestamp, row['id']))
            self._log(con, row['id'], row['task_id'], 'running', '开始识别',
                      f"第 {int(row['attempt']) + 1} 次处理", 'running', event_at=timestamp)
            self._refresh(con, row['task_id'])
            return dict(con.execute('SELECT * FROM recognition_jobs WHERE id=?', (row['id'],)).fetchone())

    def update_progress(self, claimed, event):
        with self.database.connect() as con:
            con.execute('PRAGMA busy_timeout=1000')
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT task_id,progress_json FROM recognition_jobs WHERE id=? AND status='running' AND attempt=?",
                              (claimed['id'], claimed['attempt'])).fetchone()
            if row is None:
                return False
            previous = json.loads(row['progress_json'])
            same_stage = previous.get('provider') == event['provider'] and (
                previous.get('stage') == event['stage']
                or {previous.get('stage'), event['stage']} <= {'waiting', 'paused'})
            progress = {**event, 'started_at': previous['started_at'] if same_stage else event['updated_at']}
            con.execute('UPDATE recognition_jobs SET progress_json=? WHERE id=?',
                        (json.dumps(progress, ensure_ascii=False), claimed['id']))
            if not same_stage:
                self._log(con, claimed['id'], row['task_id'], 'progress',
                          self._progress_title(event), self._progress_detail(event),
                          'running', event, event_at=event.get('updated_at'))
        return True

    def is_paused(self):
        with self.database.connect() as con:
            return bool(con.execute('SELECT paused FROM queue_control WHERE id=1').fetchone()[0])

    def control(self):
        with self.database.connect() as con:
            con.execute('BEGIN')
            control = dict(con.execute('SELECT paused,updated_at FROM queue_control WHERE id=1').fetchone())
            counts = {row['public_status']: row['n'] for row in con.execute(
                f"SELECT {PUBLIC_STATUS_SQL} public_status,COUNT(*) n FROM recognition_jobs WHERE status IN ('running','queued','awaiting_upload') GROUP BY public_status")}
        paused = bool(control['paused'])
        return {**control, 'paused': paused, 'running': counts.get('running', 0),
                'queued': counts.get('queued', 0), 'ready': counts.get('ready', 0),
                'awaiting_upload': counts.get('awaiting_upload', 0),
                'status': ('pausing' if counts.get('running') else 'paused') if paused else 'active'}

    def set_paused(self, paused):
        if not isinstance(paused, bool):
            raise ValueError('暂停状态必须为布尔值')
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('UPDATE queue_control SET paused=?,updated_at=? WHERE id=1', (int(paused), now_iso()))
        return self.control()

    def finish(self, claimed, result=None, preview_name='', error=''):
        """Commit the result and final job state together, preventing duplicate rows on restart."""
        reconcile = ''
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (claimed['id'],)).fetchone()
            if job is None or job['status'] != 'running' or job['attempt'] != claimed['attempt']:
                return False
            result_id = job['result_id']
            target = job['target_result_id']
            status = 'failed' if error else 'succeeded'
            if target:
                current = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (target,)).fetchone()
                if current is None:
                    status, error = 'cancelled', '原记录已删除，已取消重新识别'
                elif result_revision(current, legacy=not job['source_revision'].startswith('v2:')) != job['source_revision']:
                    status, error = 'failed', '原记录在排队或识别期间已更新，已保留最新复核结果；请确认后重试'
                elif not error:
                    result.setdefault('queue', {}).update(job_id=job['id'], attempt=job['attempt'])
                    self.database.replace_after_retry(target, result, preview_name, _connection=con)
                    result_id = target
                    reconcile = current['task_id']
            else:
                if error:
                    options = json.loads(job['payload_json'])
                    result = {'overall': '识别失败', 'final_result': '识别失败', 'review_status': '待复核',
                              'review_reasons': ['识别流程异常'], 'fields': {}, 'field_metadata': {},
                              'date_check': {'status': '未识别', 'actual': '', 'reliable': False},
                              'seal_check': {'status': '未识别', 'recognized': '', 'reliable': False},
                              'processing_artifacts': {'date': [], 'seals': []}, 'processing_seconds': 0,
                              'ocr_backend': options.get('ocr_backend', ''),
                              'recognition_config': options.get('recognition_config'),
                              'seal_recognition_mode': options.get('seal_recognition_mode', 'local')}
                result.setdefault('queue', {}).update(job_id=job['id'], attempt=job['attempt'])
                result_id = self.database.insert_result(filename=job['filename'], stored_name=job['stored_name'],
                    preview_name=preview_name if not error else '', task_id=job['task_id'], result=result,
                    error_type='识别失败' if error else '', error_message=error, _connection=con)
                reconcile = job['task_id']
            timestamp = now_iso()
            con.execute('UPDATE recognition_jobs SET status=?,finished_at=?,result_id=?,error_message=? WHERE id=?',
                        (status, timestamp, result_id, str(error), job['id']))
            title = {'succeeded': '处理完成', 'failed': '处理失败', 'cancelled': '处理已取消'}[status]
            detail = str(error) if error else (f"生成结果 #{result_id}" if result_id else '')
            self._log(con, job['id'], job['task_id'], status, title, detail, status,
                      {'result_id': result_id} if result_id else {}, event_at=timestamp)
            self._refresh(con, job['task_id'])
            if reconcile:
                self.database.reconcile_task_pages(reconcile, filename=job['filename'], _connection=con)
        return True

    @staticmethod
    def _counts(con, task_id):
        counts = {row['public_status']: row['n'] for row in con.execute(
            f'SELECT {PUBLIC_STATUS_SQL} public_status,COUNT(*) n FROM recognition_jobs WHERE task_id=? GROUP BY public_status', (task_id,))}
        pending = con.execute("""SELECT COUNT(DISTINCT r.id) FROM recognition_jobs j JOIN results r
            ON r.id=COALESCE(j.result_id,j.target_result_id)
            WHERE j.task_id=? AND r.deleted_at='' AND r.review_status='待复核'""", (task_id,)).fetchone()[0]
        active = sum(counts.get(key, 0) for key in ACTIVE)
        status = ('处理中' if counts.get('running') else '等待识别' if counts.get('queued')
                  else '等待上传' if counts.get('awaiting_upload') else '待开始' if counts.get('ready') else '已完成')
        return dict(status=status, total=sum(counts.values()), completed=sum(counts.values()) - active,
                    succeeded=counts.get('succeeded', 0), failed=counts.get('failed', 0),
                    cancelled=counts.get('cancelled', 0), pending_review=pending,
                    awaiting_upload=counts.get('awaiting_upload', 0), ready=counts.get('ready', 0),
                    queued=counts.get('queued', 0),
                    running=counts.get('running', 0))

    def _refresh(self, con, task_id):
        counts = self._counts(con, task_id)
        con.execute('''UPDATE batch_tasks SET status=?,updated_at=?,completed=?,succeeded=?,failed=?,pending_review=?
            WHERE id=?''', (counts['status'], now_iso(), counts['completed'], counts['succeeded'],
                           counts['failed'], counts['pending_review'], task_id))

    def task(self, task_id):
        with self.database.connect() as con:
            task = con.execute('SELECT * FROM batch_tasks WHERE id=?', (task_id,)).fetchone()
            if task is None:
                raise KeyError(task_id)
            output = dict(task)
            output.update(self._counts(con, task_id))
            output['items'] = [self.public(dict(row)) for row in con.execute(
                'SELECT * FROM recognition_jobs WHERE task_id=? ORDER BY CAST(client_key AS INTEGER)', (task_id,))]
        return output

    @staticmethod
    def _history_event(row):
        try:
            metadata = json.loads(row['metadata_json'] or '{}')
        except json.JSONDecodeError:
            metadata = {}
        return {'time': row['event_at'], 'kind': row['event_type'], 'title': row['title'],
                'detail': row['detail'], 'status': row['status'], 'metadata': metadata}

    @staticmethod
    def _review_event(row):
        detail = row['note'] or row['error_type']
        try:
            after = json.loads(row['after_json'] or '{}')
        except json.JSONDecodeError:
            after = {}
        final = after.get('final_result') or after.get('overall') or ''
        review = after.get('review_status') or ''
        if final or review:
            detail = ' · '.join(part for part in (detail, review, final) if part)
        return {'time': row['changed_at'], 'kind': 'review', 'title': row['action'] or '人工复核',
                'detail': detail, 'status': review or 'review', 'metadata': {'result_id': row['result_id']}}

    @staticmethod
    def _job_fallback_events(job):
        events = [{'time': job['created_at'], 'kind': 'submitted', 'title': '已提交文件',
                   'detail': job['filename'], 'status': 'awaiting_upload', 'metadata': {'job_id': job['id']}}]
        if job['uploaded_at']:
            events.append({'time': job['uploaded_at'], 'kind': 'uploaded', 'title': '文件已上传',
                           'detail': '已保存到本地，等待开始识别', 'status': 'ready',
                           'metadata': {'job_id': job['id']}})
        if job['start_requested'] and job['uploaded_at']:
            events.append({'time': job['uploaded_at'], 'kind': 'queued', 'title': '已加入识别队列',
                           'detail': '等待后台处理', 'status': 'queued', 'metadata': {'job_id': job['id']}})
        if job['started_at']:
            events.append({'time': job['started_at'], 'kind': 'running', 'title': '开始识别',
                           'detail': f"第 {job['attempt']} 次处理", 'status': 'running',
                           'metadata': {'job_id': job['id']}})
        try:
            progress = json.loads(job['progress_json'] or '{}')
        except json.JSONDecodeError:
            progress = {}
        if progress and not job['finished_at']:
            events.append({'time': progress.get('updated_at') or job['started_at'], 'kind': 'progress',
                           'title': JobStore._progress_title(progress),
                           'detail': JobStore._progress_detail(progress), 'status': 'running',
                           'metadata': progress})
        if job['finished_at']:
            title = {'succeeded': '处理完成', 'failed': '处理失败', 'cancelled': '处理已取消'}.get(job['status'], job['status'])
            detail = job['error_message'] or (f"生成结果 #{job['result_id']}" if job['result_id'] else '')
            events.append({'time': job['finished_at'], 'kind': job['status'], 'title': title,
                           'detail': detail, 'status': job['status'], 'metadata': {'job_id': job['id']}})
        return [event for event in events if event['time']]

    @staticmethod
    def _result_fallback_events(result_row):
        return [
            {'time': result_row['created_at'], 'kind': 'submitted', 'title': '已提交文件',
             'detail': result_row['filename'], 'status': 'submitted', 'metadata': {'result_id': result_row['id']}},
            {'time': result_row['created_at'], 'kind': 'succeeded' if not result_row['error_message'] else 'failed',
             'title': '处理完成' if not result_row['error_message'] else '处理失败',
             'detail': result_row['error_message'] or result_row['final_result'] or result_row['overall'],
             'status': 'succeeded' if not result_row['error_message'] else 'failed',
             'metadata': {'result_id': result_row['id']}},
        ]

    def process_history(self, *, job_id='', result_id=0):
        if not job_id and not result_id:
            raise KeyError('process-history')
        with self.database.connect() as con:
            result_row = None
            if result_id:
                result_row = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
                if result_row is None:
                    raise KeyError(result_id)

            jobs = []
            if job_id:
                job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
                if job is None:
                    raise KeyError(job_id)
                jobs.append(job)
                linked_result = job['result_id'] or job['target_result_id']
                if result_row is None and linked_result:
                    result_row = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (linked_result,)).fetchone()
                    result_id = linked_result if result_row is not None else 0
            elif result_row is not None:
                try:
                    payload = json.loads(result_row['result_json'] or '{}')
                except json.JSONDecodeError:
                    payload = {}
                queue_job_id = (payload.get('queue') or {}).get('job_id')
                if queue_job_id:
                    rows = con.execute('''SELECT * FROM recognition_jobs
                        WHERE id=? OR result_id=? OR target_result_id=?
                        ORDER BY created_at,rowid''', (queue_job_id, result_id, result_id)).fetchall()
                else:
                    rows = con.execute('''SELECT * FROM recognition_jobs
                        WHERE result_id=? OR target_result_id=?
                        ORDER BY created_at,rowid''', (result_id, result_id)).fetchall()
                jobs = rows

            events = []
            for job in jobs:
                history = con.execute('''SELECT * FROM recognition_job_history
                    WHERE job_id=? ORDER BY id''', (job['id'],)).fetchall()
                events.extend([self._history_event(row) for row in history] if history else self._job_fallback_events(job))

            if result_row is not None:
                if not events:
                    events.extend(self._result_fallback_events(result_row))
                reviews = con.execute('SELECT * FROM review_history WHERE result_id=? ORDER BY id', (result_row['id'],)).fetchall()
                events.extend(self._review_event(row) for row in reviews)

        indexed = [(index, event) for index, event in enumerate(events)]
        events = [event for _, event in sorted(indexed, key=lambda item: (item[1].get('time') or '', item[0]))]
        filename = result_row['filename'] if result_row is not None else jobs[0]['filename']
        current_status = ''
        if result_row is not None:
            current_status = result_row['final_result'] or result_row['overall']
        elif jobs:
            current_status = self.public(dict(jobs[-1]))['status']
        return {'filename': filename, 'job_id': job_id or (jobs[-1]['id'] if jobs else ''),
                'result_id': result_row['id'] if result_row is not None else result_id,
                'current_status': current_status, 'events': events}

    def daily(self, day):
        with self.database.connect() as con:
            return [self.public(dict(row)) for row in con.execute('''SELECT j.*,r.review_status,r.final_result FROM recognition_jobs j
                JOIN batch_tasks t ON t.id=j.task_id
                LEFT JOIN results r ON r.id=COALESCE(j.result_id,j.target_result_id) AND r.deleted_at=''
                WHERE substr(t.created_at,1,10)=?
                  AND (COALESCE(j.result_id,j.target_result_id) IS NULL OR r.id IS NOT NULL)
                ORDER BY j.status IN ('queued','running') DESC,j.created_at DESC,j.rowid DESC''', (day,))]
