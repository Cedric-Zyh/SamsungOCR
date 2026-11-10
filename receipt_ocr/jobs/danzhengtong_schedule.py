"""Indexed scheduling projections; provider evidence is decoded only on change."""
from datetime import datetime
import json


LIVE = "active=1 AND status NOT IN ('applied','conflict','cancelled')"
SOURCE_COLUMNS = (
    'active', 'status', 'job_id', 'result_id', 'source_revision', 'seal_requests_json',
    'req_uuid', 'result_json', 'submission_json', 'recognition_call_json',
    'submitted_at', 'next_query_at',
)


def deadline(value):
    """Match branch_due: missing, malformed and naive legacy dates are due."""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def initialize_schedule(con):
    columns = {row['name'] for row in con.execute('PRAGMA table_info(danzhengtong_jobs)')}
    additions = {
        'dispatch_version': 'INTEGER NOT NULL DEFAULT 0',
        'dispatch_dirty': 'INTEGER NOT NULL DEFAULT 1',
        'dispatch_submit': 'INTEGER NOT NULL DEFAULT 0',
        'dispatch_query_due': 'REAL',
        'dispatch_apply_due': 'REAL',
    }
    for name, definition in additions.items():
        if name not in columns:
            con.execute(f'ALTER TABLE danzhengtong_jobs ADD COLUMN {name} {definition}')
    changed = ' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in SOURCE_COLUMNS)
    con.execute(f'''CREATE TRIGGER IF NOT EXISTS invalidate_dzt_dispatch
        AFTER UPDATE OF {','.join(SOURCE_COLUMNS)} ON danzhengtong_jobs
        WHEN {changed}
        BEGIN UPDATE danzhengtong_jobs
            SET dispatch_dirty=1,dispatch_version=dispatch_version+1 WHERE id=NEW.id; END''')
    con.execute(f'''CREATE INDEX IF NOT EXISTS idx_dzt_dispatch_dirty
        ON danzhengtong_jobs(created_at,id) WHERE {LIVE} AND dispatch_dirty=1''')
    for kind, columns in (
        ('submit', 'dispatch_submit,last_query_at,created_at,id'),
        ('query', 'dispatch_query_due,last_query_at,created_at,id'),
        ('apply', 'dispatch_apply_due,last_query_at,created_at,id'),
        ('manual', 'manual_query,last_query_at,created_at,id'),
    ):
        con.execute(f'''CREATE INDEX IF NOT EXISTS idx_dzt_dispatch_{kind}
            ON danzhengtong_jobs({columns}) WHERE {LIVE} AND dispatch_dirty=0''')


def normalized_task(task):
    from .danzhengtong_store import remote_state, aggregate_remote
    state = remote_state(task)
    aggregate = aggregate_remote(state)
    if task['status'] == 'received' and aggregate['status'] == 'received':
        aggregate['next_query_at'] = task['next_query_at']
    return {**task, **aggregate, 'seal_requests_json': json.dumps(state, ensure_ascii=False)}, state


def projection(task, state):
    from .danzhengtong_store import next_remote_operation
    if task['status'] == 'received':
        return 0, None, deadline(task['next_query_at'])
    if task['status'] not in {'queued', 'waiting'}:
        return 0, None, None
    submit = next_remote_operation({**task, 'manual_query': 0}, state,
        {'submit_paused': False, 'auto_query': False}, prefer_submit=True)
    queries = []
    page = state['page']
    if state.get('page_required') and page.get('status') == 'waiting' and task.get('req_uuid'):
        queries.append(deadline(page.get('next_query_at')))
    queries.extend(deadline(child.get('next_query_at')) for child in state['requests']
                   if child.get('status') == 'waiting' and child.get('req_uuid'))
    return int(submit is not None), min(queries) if queries else None, None


def refresh_dirty(database):
    """Decode outside write transactions, then compare-and-set small projections.

    A bounded batch also lets newly queued work start during large upgrades.
    Changed versions stay dirty and are recomputed on the next pass.
    """
    for _ in range(8):
        with database.connect() as con:
            rows = con.execute(f'''SELECT * FROM danzhengtong_jobs
                WHERE {LIVE} AND dispatch_dirty=1 ORDER BY created_at,id LIMIT 128''').fetchall()
        if not rows:
            return
        updates = []
        for row in rows:
            task, state = normalized_task(dict(row))
            updates.append((*projection(task, state), row['id'], row['dispatch_version']))
        with database.connect() as con:
            con.executemany('''UPDATE danzhengtong_jobs SET dispatch_submit=?,dispatch_query_due=?,
                dispatch_apply_due=?,dispatch_dirty=0 WHERE id=? AND dispatch_version=? AND dispatch_dirty=1''', updates)
        if len(rows) < 128:
            return


def candidate(con, control, timestamp, prefer_submit):
    """Compare only the first eligible item from each priority lane."""
    base = '''d.active=1 AND d.status NOT IN ('applied','conflict','cancelled') AND d.dispatch_dirty=0
        AND (d.lease_until='' OR d.lease_until<?)
        AND (d.result_id IS NULL OR EXISTS(SELECT 1 FROM results r WHERE r.id=d.result_id AND r.deleted_at=''))'''
    lanes = [
        (0, 'manual', 'd.manual_query=1', ()),
        (0, 'apply', '''d.dispatch_apply_due<=? AND
            (j.status IN ('succeeded','failed') OR (j.status='queued'
                AND json_extract(j.payload_json,'$._execution_scope')='danzhengtong'))''', (deadline(timestamp),)),
    ]
    if not control['submit_paused']:
        lanes.append((1 if prefer_submit else 2, 'submit',
                      'd.dispatch_submit=1 AND d.manual_query=0', ()))
    if control['auto_query']:
        lanes.append((2 if prefer_submit else 1, 'query',
                      'd.dispatch_query_due<=? AND d.manual_query=0', (deadline(timestamp),)))
    heads = []
    for priority, kind, predicate, parameters in lanes:
        row = con.execute(f'''SELECT d.id,d.last_query_at,d.created_at
            FROM danzhengtong_jobs d INDEXED BY idx_dzt_dispatch_{kind}
            JOIN recognition_jobs j ON j.id=d.job_id WHERE {base} AND {predicate}
            ORDER BY d.last_query_at,d.created_at,d.id LIMIT 1''', (timestamp, *parameters)).fetchone()
        if row:
            heads.append((priority, row['last_query_at'], row['created_at'], row['id']))
    return min(heads)[3] if heads else None
