"""Versioned queue schema; runtime recovery is owned by the worker."""
from ..persistence.migrations import execute_script, run_migrations
from ..persistence.result_versions import result_revision


def _baseline(con):
    execute_script(con, '''
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
    columns = {row['name'] for row in con.execute('PRAGMA table_info(recognition_jobs)')}
    if 'start_requested' not in columns:
        # Jobs imported before this change already had permission to run.
        con.execute('ALTER TABLE recognition_jobs ADD COLUMN start_requested INTEGER NOT NULL DEFAULT 1')
    if 'priority' not in columns:
        con.execute('ALTER TABLE recognition_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
    if 'progress_json' not in columns:
        con.execute("ALTER TABLE recognition_jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'")
    if 'branch_snapshot_json' not in columns:
        con.execute("ALTER TABLE recognition_jobs ADD COLUMN branch_snapshot_json TEXT NOT NULL DEFAULT '{}'")
    con.execute('''CREATE TRIGGER IF NOT EXISTS require_recognition_start
        BEFORE UPDATE OF status ON recognition_jobs
        WHEN NEW.status='running' AND OLD.status!='running' AND NEW.start_requested=0
        BEGIN SELECT RAISE(ABORT,'recognition_start_required'); END''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_jobs_start ON recognition_jobs(status,start_requested,uploaded_at)')


def repair_legacy_revisions(con):
    """Compatibility for legacy jobs imported after the schema was upgraded."""
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


MIGRATIONS = ((1, "recognition_queue_schema", _baseline),)


def initialize_jobs(con):
    run_migrations(con, "recognition_jobs", MIGRATIONS)
    repair_legacy_revisions(con)
