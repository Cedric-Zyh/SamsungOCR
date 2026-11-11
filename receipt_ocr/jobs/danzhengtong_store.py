"""Durable, independently scheduled single-document remote requests."""
from datetime import datetime, timedelta
from copy import deepcopy
import hashlib
import json
import uuid

from ..persistence.database_clock import now_iso

from ..persistence.job_views import PENDING, LABELS, PUBLIC_KEYS, PUBLIC_COLUMNS, remote_public
from ..persistence.result_versions import result_revision
from ..domain.remote_tasks import remote_state, aggregate_remote, branch_due, next_remote_operation
from ..domain.remote_runs import LocalAttempt, RemoteRunLink


def later(seconds):
    return (datetime.now().astimezone() + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def scope_plan(options):
    from ..application.plans import default_plan
    return deepcopy(options.get('recognition_config') or default_plan(options.get('ocr_backend'), options.get('seal_recognition_mode')))


def has_remote(plan):
    return any('danzhengtong' in value for value in plan.values() if isinstance(value, list))


def settings_identity(settings):
    keys = ('base_url', 'upload_url', 'upload_source_code', 'sys_code', 'org_id', 'model_id', 'seal_model_id')
    return {**{key: getattr(settings, key) for key in keys},
            'account': hashlib.sha256(settings.app_id.encode()).hexdigest()}


def local_attachment(job, *, retaining_local=False, remote_only=False):
    """Queued full runs attach to the upcoming claim, remote-only runs do not."""
    options = json.loads(job['payload_json'])
    upcoming = (job['status'] == 'queued' and not retaining_local and not remote_only
                and options.get('_execution_scope') != 'danzhengtong')
    return LocalAttempt(job['id'], int(job['attempt']) + int(upcoming))


def retained_remote(remote, state):
    return (remote['status'] in {'applied', 'conflict', 'failed', 'unknown', 'cancelled'}
            or bool((state.get('retained_remote_result') or {}).get('finished_at')))




class DanzhengtongOperations:
    def initialize_danzhengtong(self, con):
        from .danzhengtong_schema import initialize_remote_jobs
        initialize_remote_jobs(con)

    remote_public = staticmethod(remote_public)

    def attach_remote(self, items, con):
        rows = {}
        ids = [item['id'] for item in items]
        for offset in range(0, len(ids), 400):
            chunk = ids[offset:offset + 400]
            placeholders = ','.join('?' for _ in chunk)
            rows.update({row['job_id']: self.remote_public(row) for row in con.execute(
                f'SELECT {PUBLIC_COLUMNS} FROM danzhengtong_jobs WHERE active=1 AND job_id IN ({placeholders})', chunk)})
        for item in items:
            item['danzhengtong'] = rows.get(item['id'])
        return items

    def ensure_remote(self, con, job, *, resubmit=False, plan=None, independent=False):
        existing = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1', (job['id'],)).fetchone()
        if existing and not resubmit:
            return existing['id']
        if resubmit and existing and existing['lease_until']:
            raise ValueError('单证通请求正在执行，请稍后重新提交')
        if resubmit:
            con.execute('UPDATE danzhengtong_jobs SET active=0 WHERE job_id=?', (job['id'],))
        timestamp, remote_id = now_iso(), uuid.uuid4().hex
        saved = con.execute('SELECT payload_json FROM recognition_jobs WHERE id=?', (job['id'],)).fetchone()
        job = {**dict(job), 'payload_json': saved['payload_json']}
        if plan is None:
            plan = scope_plan(json.loads(saved['payload_json']))
        result_id = job['result_id'] or job['target_result_id']
        revision = ''
        if result_id:
            result = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
            if result is None:
                raise ValueError('原记录已删除，不能提交单证通')
            revision = result_revision(result)
            if not resubmit:
                previous = con.execute('''SELECT * FROM danzhengtong_jobs
                    WHERE result_id=? AND active=1 AND job_id<>? ORDER BY created_at DESC LIMIT 1''',
                    (result_id, job['id'])).fetchone()
                if previous:
                    # A regular retry is a new local job, not permission to
                    # upload the same remote request again. Transfer its durable
                    # continuation, including a POST whose reply is still in flight.
                    state = remote_state(dict(previous))
                    from ..persistence.async_recognition_calls import combined_remote_call
                    recorded = combined_remote_call(dict(previous), job['filename']) or {}
                    timing = recorded.get('transport') or {}
                    retained = state.get('retained_remote_result') or {}
                    completed = (timing.get('applied_at') if previous['status'] in {'applied', 'conflict'} else
                                 retained.get('finished_at') if retained.get('job_id') == previous['job_id'] else None)
                    if completed:
                        # Applying this same result to another local retry will
                        # update applied_at; preserve its original completion.
                        state['retained_remote_result'] = {'job_id': job['id'], 'finished_at': completed}
                    link = RemoteRunLink.from_json(state.get('run_link'))
                    if link is not None:
                        state['run_link'] = link.attach(local_attachment(job),
                            completed=retained_remote(previous, state)).to_json()
                    aggregate = aggregate_remote(state)
                    con.execute('''UPDATE danzhengtong_jobs SET job_id=?,source_revision=?,status=?,
                        next_query_at=?,seal_requests_json=? WHERE id=?''',
                        (job['id'], revision, aggregate['status'], aggregate['next_query_at'],
                         json.dumps(state, ensure_ascii=False), previous['id']))
                    return previous['id']
        seal_required = 'danzhengtong' in plan.get('seal', [])
        page_required = any('danzhengtong' in plan.get(stage, []) for stage in ('fields', 'handwriting', 'date'))
        seals = {'version': 2, 'prepared': not seal_required, 'requests': [],
                 'seal_required': seal_required, 'page_required': page_required,
                 'page': {'status': 'queued' if page_required else 'skipped', 'error': '', 'next_query_at': ''}}
        retains_local = resubmit or independent and job['status'] in {'succeeded', 'failed'}
        seals['run_link'] = RemoteRunLink.create(remote_id,
            local_attachment(job, retaining_local=retains_local,
                             remote_only=independent and job['status'] == 'queued' and not job['start_requested']),
            retains_local=retains_local,
            replaces_remote_id=existing['id'] if resubmit and existing else '').to_json()
        if retains_local:
            # This is a new provider run reusing the job's local evidence. Keep
            # its provenance on the remote attempt, not on the reusable job.
            seals['retained_regular_job_id'] = job['id']
            seals['retained_regular_attempt'] = job['attempt']
        con.execute('''INSERT INTO danzhengtong_jobs
            (id,job_id,created_at,updated_at,result_id,source_revision,plan_json,seal_requests_json) VALUES(?,?,?,?,?,?,?,?)''',
            (remote_id, job['id'], timestamp, timestamp, result_id, revision, json.dumps(plan, ensure_ascii=False),
             json.dumps(seals, ensure_ascii=False)))
        self._log(con, job['id'], job['task_id'], 'dzt_queued', '单证通已加入提交队列', status='queued')
        return remote_id

    def bind_remote_run(self, con, job, attempt):
        """Attach a local retry without calling or resubmitting its remote lane."""
        remote = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                             (job['id'],)).fetchone()
        if remote is None:
            return
        state = json.loads(remote['seal_requests_json'] or '{}')
        link = RemoteRunLink.from_json(state.get('run_link'))
        if link is None:
            return  # Legacy ownership remains conservative; do not invent it.
        attached = link.attach(LocalAttempt(job['id'], attempt), completed=retained_remote(remote, state))
        if attached != link:
            state['run_link'] = attached.to_json()
            con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                        (json.dumps(state, ensure_ascii=False), remote['id']))

    def remote_control(self, changes=None):
        with self.database.connect() as con:
            if changes is not None:
                if not changes or set(changes) - {'submit_paused', 'auto_query'} or any(type(v) is not bool for v in changes.values()):
                    raise ValueError('单证通控制参数必须为布尔值')
                con.execute('BEGIN IMMEDIATE')
                for key, value in changes.items():
                    con.execute(f'UPDATE danzhengtong_control SET {key}=? WHERE id=1', (int(value),))
            row = con.execute('SELECT * FROM danzhengtong_control WHERE id=1').fetchone()
            return {key: bool(row[key]) for key in ('submit_paused', 'auto_query')}

    def remote_action(self, ids, action):
        if action not in ('query', 'submit', 'resubmit'):
            raise ValueError('不支持的单证通操作')
        if (not isinstance(ids, list) or not ids or len(ids) > 5000 or
                any(not isinstance(value, str) or not value for value in ids)):
            raise ValueError('请选择有效的单证通任务')
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            jobs = []
            for job_id in dict.fromkeys(ids):
                job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
                if job is None:
                    raise KeyError(job_id)
                if not job['stored_name'] or job['status'] in ('awaiting_upload', 'cancelled'):
                    raise ValueError('请先完成图片导入')
                if job['result_id'] or job['target_result_id']:
                    if con.execute("SELECT 1 FROM results WHERE id=? AND deleted_at=''",
                                   (job['result_id'] or job['target_result_id'],)).fetchone() is None:
                        raise ValueError('原记录已删除')
                jobs.append(job)
            for job in jobs:
                if action in ('submit', 'resubmit'):
                    # Explicitly requesting this provider authorizes its four
                    # supported contents even if the regular plan omits it.
                    payload = json.loads(job['payload_json'])
                    plan = scope_plan(payload)
                    for stage in ('fields', 'handwriting', 'date', 'seal'):
                        plan[stage] = list(dict.fromkeys([*plan[stage], 'danzhengtong']))
                    payload['recognition_config'] = plan
                    payload['_independent_dzt'] = True
                    con.execute('UPDATE recognition_jobs SET payload_json=? WHERE id=?',
                                (json.dumps(payload, ensure_ascii=False), job['id']))
                    self.ensure_remote(con, job, resubmit=action == 'resubmit', independent=True)
                    if job['status'] == 'queued' and not job['start_requested']:
                        payload['_execution_scope'] = 'danzhengtong'
                        con.execute('UPDATE recognition_jobs SET payload_json=?,start_requested=1 WHERE id=?',
                                    (json.dumps(payload, ensure_ascii=False), job['id']))
                else:
                    con.execute("""UPDATE danzhengtong_jobs SET manual_query=1,next_query_at='',updated_at=?
                        WHERE job_id=? AND active=1 AND (req_uuid<>'' OR seal_requests_json<>'{}')
                        AND status IN ('queued','waiting','received')""", (now_iso(), job['id']))
        return {'accepted': len(jobs)}

    def recover_remote(self):
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = con.execute("SELECT * FROM danzhengtong_jobs WHERE active=1 AND status NOT IN ('applied','conflict','cancelled')").fetchall()
            for row in rows:
                task = dict(row)
                state = remote_state(task, recover=True)
                aggregate = aggregate_remote(state)
                if task['status'] == 'received' and aggregate['status'] == 'received':
                    aggregate['next_query_at'] = task['next_query_at']
                con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=?,status=?,next_query_at=?,error_message=?,lease_until=?,updated_at=? WHERE id=?',
                            (json.dumps(state, ensure_ascii=False), aggregate['status'], aggregate['next_query_at'],
                             aggregate['error_message'], '', now_iso(), task['id']))
            con.execute("UPDATE danzhengtong_jobs SET lease_until='' WHERE active=1")

    def claim_remote(self):
        from .danzhengtong_schedule import candidate, normalized_task, projection, refresh_dirty
        # Read and normalize evidence without owning the database write lock.
        # Only changed tasks need projection work; idle polls use small indexes.
        for _ in range(8):
            refresh_dirty(self.database)
            timestamp = now_iso()
            prefer_submit = getattr(self, '_remote_prefer_submit', False)
            with self.database.connect() as con:
                control = dict(con.execute('SELECT * FROM danzhengtong_control WHERE id=1').fetchone())
                selected = candidate(con, control, timestamp, prefer_submit)
                if selected is None:
                    return None
                row = con.execute('''SELECT d.*,j.status AS regular_status,j.payload_json AS regular_options
                    FROM danzhengtong_jobs d JOIN recognition_jobs j ON j.id=d.job_id WHERE d.id=?''',
                    (selected,)).fetchone()
            if row is None:
                continue
            task, state = normalized_task(dict(row))
            operation = None
            if task['status'] == 'received':
                options = json.loads(task['regular_options'])
                can_apply = (task['regular_status'] in {'succeeded', 'failed'}
                             or task['regular_status'] == 'queued' and options.get('_execution_scope') == 'danzhengtong')
                if can_apply and branch_due(task['next_query_at'], timestamp):
                    operation = ('apply', None)
            elif task['status'] in {'queued', 'waiting'}:
                operation = next_remote_operation(task, state, control, timestamp=timestamp, prefer_submit=prefer_submit)
            schedule = projection(task, state)
            with self.database.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                current = con.execute('''SELECT d.dispatch_version,d.dispatch_dirty,d.manual_query,
                    j.status AS regular_status,j.payload_json AS regular_options
                    FROM danzhengtong_jobs d JOIN recognition_jobs j ON j.id=d.job_id
                    WHERE d.id=? AND d.active=1 AND (d.lease_until='' OR d.lease_until<?)
                    AND (d.result_id IS NULL OR EXISTS(SELECT 1 FROM results r WHERE r.id=d.result_id AND r.deleted_at=''))''',
                    (selected, timestamp)).fetchone()
                current_control = dict(con.execute('SELECT * FROM danzhengtong_control WHERE id=1').fetchone())
                if (current is None or current['dispatch_dirty']
                        or current['dispatch_version'] != row['dispatch_version']
                        or current['manual_query'] != row['manual_query'] or current_control != control
                        or current['regular_status'] != row['regular_status']
                        or current['regular_options'] != row['regular_options']):
                    continue
                token = later(180) if operation else ''
                con.execute('''UPDATE danzhengtong_jobs SET seal_requests_json=?,status=?,next_query_at=?,
                    error_message=?,lease_until=?,manual_query=0 WHERE id=?''',
                    (task['seal_requests_json'], task['status'], task['next_query_at'],
                     task['error_message'], token, selected))
                # Normalization may invalidate the old projection; these values
                # were computed from exactly the state just committed above.
                con.execute('''UPDATE danzhengtong_jobs SET dispatch_submit=?,dispatch_query_due=?,
                    dispatch_apply_due=?,dispatch_dirty=0 WHERE id=?''', (*schedule, selected))
            if not token:
                continue
            self._remote_prefer_submit = operation[0].endswith('_query')
            return {**task, 'lease_until': token, '_operation': operation[0], '_child_index': operation[1],
                    '_manual_query': bool(task['manual_query']),
                    '_query_only': bool(task['manual_query'] or control['submit_paused'])}
        return None

    def update_remote(self, claimed, **changes):
        allowed = {'status', 'file_id', 'req_uuid', 'source_hash', 'environment_json', 'submission_json', 'recognition_call_json', 'seal_requests_json',
                   'result_json', 'submitted_at', 'last_query_at', 'query_count', 'next_query_at', 'error_message', 'lease_until'}
        if set(changes) - allowed:
            raise ValueError('无效的单证通任务属性')
        changes['updated_at'] = now_iso()
        assignments, values = [], []
        for key, value in changes.items():
            if key == 'seal_requests_json':
                # A local retry may attach this accepted remote request while
                # its network operation is in flight. The worker owns branch
                # progress, but must not overwrite the newer run association.
                assignments.append("""seal_requests_json=CASE
                    WHEN json_type(seal_requests_json,'$.run_link')='object'
                    THEN json_set(?,'$.run_link',json_extract(seal_requests_json,'$.run_link'))
                    ELSE ? END""")
                values.extend((value, value))
            else:
                assignments.append(key + '=?')
                values.append(value)
        with self.database.connect() as con:
            updated = con.execute(f"UPDATE danzhengtong_jobs SET {','.join(assignments)} WHERE id=? AND lease_until=? AND active=1",
                                  [*values, claimed['id'], claimed['lease_until']]).rowcount == 1
            if updated:
                owner = con.execute('SELECT job_id FROM danzhengtong_jobs WHERE id=?', (claimed['id'],)).fetchone()
                self.database.sync_remote_recognition_calls(con, owner['job_id'])
            return updated

    def save_branch(self, con, job, result_id, snapshot, result):
        if snapshot is not None:
            con.execute('UPDATE recognition_jobs SET branch_snapshot_json=? WHERE id=?',
                        (json.dumps({'snapshot': snapshot, 'base': result}, ensure_ascii=False), job['id']))
        row = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
        if row:
            con.execute('''UPDATE danzhengtong_jobs SET result_id=?,source_revision=?,
                status=CASE WHEN result_json<>'{}' AND (seal_requests_json='{}' OR json_extract(seal_requests_json,'$.complete')=1) THEN 'received' ELSE status END,
                next_query_at=CASE WHEN result_json<>'{}' AND (seal_requests_json='{}' OR json_extract(seal_requests_json,'$.complete')=1) THEN '' ELSE next_query_at END
                WHERE job_id=? AND active=1''',
                        (result_id, result_revision(row), job['id']))

    def remote_detail(self, job_id):
        with self.database.connect() as con:
            row = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return {**self.remote_public(row), 'page_req_uuid': row['req_uuid'],
                    'result': json.loads(row['result_json']),
                    'submission': json.loads(row['submission_json']),
                    'seal_result': json.loads(row['seal_requests_json'] or '{}')}

    @staticmethod
    def mark_remote_applied(con, remote_id):
        """Record completion only within the transaction that saved the result."""
        from ..persistence.recognition_calls import trace_timestamp
        row = con.execute('SELECT recognition_call_json,seal_requests_json FROM danzhengtong_jobs WHERE id=?',
                          (remote_id,)).fetchone()
        call = json.loads(row['recognition_call_json'] or '{}') if row else {}
        timing = call.get('transport')
        if isinstance(timing, dict):
            timing['applied_at'] = trace_timestamp()
            con.execute('UPDATE danzhengtong_jobs SET recognition_call_json=? WHERE id=?',
                        (json.dumps(call, ensure_ascii=False), remote_id))
        if row:
            state = json.loads(row['seal_requests_json'] or '{}')
            for child in state.get('requests', []):
                transport = (child.get('call') or {}).get('transport')
                if isinstance(transport, dict):
                    transport['applied_at'] = trace_timestamp()
            if state:
                con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                            (json.dumps(state, ensure_ascii=False), remote_id))

    def apply_remote(self, claimed, combined):
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            remote = con.execute('SELECT * FROM danzhengtong_jobs WHERE id=? AND active=1 AND lease_until=?',
                                 (claimed['id'], claimed['lease_until'])).fetchone()
            if remote is None:
                return
            if remote['source_revision'] != claimed['source_revision']:
                # The regular branch committed a newer snapshot while this
                # result was being combined. Recombine that snapshot next.
                con.execute("UPDATE danzhengtong_jobs SET lease_until='',next_query_at='' WHERE id=?", (remote['id'],))
                return
            job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (remote['job_id'],)).fetchone()
            if job['status'] not in ('succeeded', 'failed'):
                con.execute("UPDATE danzhengtong_jobs SET status='received',lease_until='',next_query_at=? WHERE id=?", (later(2), remote['id']))
                return
            current = con.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (remote['result_id'],)).fetchone()
            latest = con.execute('SELECT id FROM recognition_jobs WHERE COALESCE(result_id,target_result_id)=? ORDER BY rowid DESC LIMIT 1',
                                 (remote['result_id'],)).fetchone()
            conflict = (current is None or result_revision(current) != remote['source_revision']
                        or (latest and latest['id'] != job['id']) or job['status'] == 'running')
            if conflict:
                status, message = 'conflict', '单据已修改或已有新识别，已保存单证通结果，请核对后使用'
            else:
                status, message = 'applied', ''
                self.database.result_writes.apply_remote_result(current['id'], combined, connection=con)
                if job['status'] == 'failed':
                    con.execute("UPDATE recognition_jobs SET status='succeeded',error_message='' WHERE id=?", (job['id'],))
            con.execute('UPDATE danzhengtong_jobs SET status=?,error_message=?,lease_until=?,updated_at=? WHERE id=?',
                        (status, message, '', now_iso(), remote['id']))
            if status == 'applied':
                self.mark_remote_applied(con, remote['id'])
            self.database.sync_remote_recognition_calls(con, job['id'])
            self._log(con, job['id'], job['task_id'], 'dzt_result', LABELS[status], message, status)
            self._refresh(con, job['task_id'])
