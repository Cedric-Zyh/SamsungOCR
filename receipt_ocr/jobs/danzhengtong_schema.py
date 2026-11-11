"""Versioned remote queue schema and scheduling projection upgrades."""
from ..persistence.migrations import execute_script, run_migrations


def _baseline(con):
    execute_script(con, '''
        CREATE TABLE IF NOT EXISTS danzhengtong_jobs (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES recognition_jobs(id) ON DELETE CASCADE,
            active INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            submitted_at TEXT NOT NULL DEFAULT '', last_query_at TEXT NOT NULL DEFAULT '',
            next_query_at TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '',
            file_id TEXT NOT NULL DEFAULT '', req_uuid TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL DEFAULT '', environment_json TEXT NOT NULL DEFAULT '{}',
            plan_json TEXT NOT NULL DEFAULT '{}',
            submission_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}',
            query_count INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '',
            result_id INTEGER, source_revision TEXT NOT NULL DEFAULT '',
            manual_query INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dzt_active ON danzhengtong_jobs(job_id) WHERE active=1;
        CREATE INDEX IF NOT EXISTS idx_dzt_due ON danzhengtong_jobs(active,status,next_query_at);
        CREATE TABLE IF NOT EXISTS danzhengtong_control (
            id INTEGER PRIMARY KEY CHECK(id=1), submit_paused INTEGER NOT NULL DEFAULT 0,
            auto_query INTEGER NOT NULL DEFAULT 1
        );
        INSERT OR IGNORE INTO danzhengtong_control(id) VALUES(1);
        CREATE TRIGGER IF NOT EXISTS cancel_dzt_deleted_result AFTER DELETE ON results
        BEGIN UPDATE danzhengtong_jobs SET active=0,status='cancelled',lease_until=''
            WHERE result_id=OLD.id; END;
        CREATE TRIGGER IF NOT EXISTS cancel_dzt_soft_deleted_result AFTER UPDATE OF deleted_at ON results
        WHEN NEW.deleted_at<>''
        BEGIN UPDATE danzhengtong_jobs SET active=0,status='cancelled',lease_until=''
            WHERE result_id=NEW.id; END;
    ''')
    columns = {row['name'] for row in con.execute('PRAGMA table_info(danzhengtong_jobs)')}
    if 'plan_json' not in columns:
        con.execute("ALTER TABLE danzhengtong_jobs ADD COLUMN plan_json TEXT NOT NULL DEFAULT '{}'")
    if 'recognition_call_json' not in columns:
        con.execute("ALTER TABLE danzhengtong_jobs ADD COLUMN recognition_call_json TEXT NOT NULL DEFAULT '{}'")
    if 'seal_requests_json' not in columns:
        # An empty object identifies a legacy, single-scene continuation.
        con.execute("ALTER TABLE danzhengtong_jobs ADD COLUMN seal_requests_json TEXT NOT NULL DEFAULT '{}'")
    from .danzhengtong_schedule import initialize_schedule
    initialize_schedule(con)


def _refresh_dispatch(con):
    from .danzhengtong_schedule import initialize_schedule
    initialize_schedule(con, rebuild=True)


MIGRATIONS = (
    (1, "remote_queue_schema", _baseline),
    (2, "replace_dispatch_trigger", _refresh_dispatch),
)


def initialize_remote_jobs(con):
    run_migrations(con, "danzhengtong_jobs", MIGRATIONS)
