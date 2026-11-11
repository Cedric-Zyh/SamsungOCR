"""Upgrades are ordered, atomic, restartable and preserve recognition evidence."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from receipt_ocr.persistence.migrations import execute_script, run_migrations


def test_failed_upgrade_rolls_back_schema_data_and_registration(tmp_path):
    db = Database(tmp_path / 'upgrade.db')
    calls = []

    def first(con):
        calls.append('first')
        execute_script(con, "CREATE TABLE evidence(value TEXT); INSERT INTO evidence VALUES('a;b');")

    def failed(con):
        con.execute('ALTER TABLE evidence ADD COLUMN extra TEXT')
        raise RuntimeError('interrupted')

    with pytest.raises(RuntimeError, match='interrupted'):
        with db.connect() as con:
            run_migrations(con, 'test', ((1, 'first', first), (2, 'second', failed)))
    with db.connect() as con:
        assert not con.execute("SELECT name FROM sqlite_master WHERE name IN ('evidence','schema_migrations')").fetchall()
        run_migrations(con, 'test', ((1, 'first', first), (2, 'second', lambda con: None)))
    with db.connect() as con:
        assert con.execute('SELECT value FROM evidence').fetchone()[0] == 'a;b'
        run_migrations(con, 'test', ((1, 'first', first), (2, 'second', failed)))
        assert con.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 2
    assert calls == ['first', 'first']


def test_migration_never_commits_callers_transaction(tmp_path):
    db = Database(tmp_path / 'outer.db')
    with db.connect() as con:
        con.execute('CREATE TABLE receipt(value TEXT)')
    with db.connect() as con:
        con.execute("INSERT INTO receipt VALUES('pending')")
        run_migrations(con, 'test', ((1, 'trigger', lambda c: execute_script(c, '''
            CREATE TRIGGER copy AFTER INSERT ON receipt BEGIN
                UPDATE receipt SET value='changed' WHERE rowid=NEW.rowid;
            END;
        ''')),))
        with db.connect() as reader:
            assert reader.execute('SELECT COUNT(*) FROM receipt').fetchone()[0] == 0
        con.rollback()
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM receipt').fetchone()[0] == 0
        assert not con.execute("SELECT name FROM sqlite_master WHERE name='copy'").fetchone()


@pytest.mark.parametrize('legacy_without_ledger', [False, True])
def test_upgrade_replaces_stale_summary_trigger_and_projection(tmp_path, legacy_without_ledger):
    db = Database(tmp_path / 'receipt.db')
    db.initialize()
    result = {'fields': {'客户名称': '原客户'}, 'overall': '通过', 'review_issues': []}
    identity = db.insert_result(filename='one.png', stored_name='one.png', preview_name='', task_id='', result=result)
    with db.connect() as con:
        original = con.execute('SELECT result_json,original_result_json FROM results WHERE id=?', (identity,)).fetchone()
        if legacy_without_ledger:
            con.execute('DROP TABLE schema_migrations')
        else:
            con.execute("DELETE FROM schema_migrations WHERE component='receipts' AND version=2")
        con.execute('DROP TRIGGER results_summary_update')
        con.execute("CREATE TRIGGER results_summary_update AFTER UPDATE OF result_json ON results BEGIN SELECT 1; END")
        con.execute("UPDATE results SET summary_json='{}'")
    db.initialize()
    with db.connect() as con:
        row = con.execute('SELECT result_json,original_result_json,summary_json FROM results WHERE id=?', (identity,)).fetchone()
        assert tuple(row)[:2] == tuple(original)
        assert json.loads(row['summary_json'])['fields'] == result['fields']
        con.execute("UPDATE results SET result_json=json_set(result_json, '$.fields.客户名称', '新客户') WHERE id=?", (identity,))
        assert json.loads(con.execute('SELECT summary_json FROM results WHERE id=?', (identity,)).fetchone()[0])['fields']['客户名称'] == '新客户'
        versions = [tuple(r) for r in con.execute('SELECT component,version,name,applied_at FROM schema_migrations')]
    db.initialize()
    with db.connect() as con:
        assert [tuple(r) for r in con.execute('SELECT component,version,name,applied_at FROM schema_migrations')] == versions


def test_concurrent_database_and_queue_upgrades_run_once(tmp_path):
    path = tmp_path / 'parallel.db'

    def initialize(_):
        db = Database(path)
        db.initialize()
        JobStore(db).initialize()

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(initialize, range(3)))
    with Database(path).connect() as con:
        assert [tuple(r) for r in con.execute('SELECT component,COUNT(*) FROM schema_migrations GROUP BY component ORDER BY component')] == [
            ('danzhengtong_jobs', 2), ('receipts', 2), ('recognition_jobs', 1)]


def test_newer_or_rewritten_migration_is_rejected(tmp_path):
    db = Database(tmp_path / 'version.db')
    with db.connect() as con:
        run_migrations(con, 'test', ((1, 'one', lambda c: None),))
    with db.connect() as con:
        with pytest.raises(RuntimeError, match='不兼容'):
            run_migrations(con, 'test', ((1, 'renamed', lambda c: None),))
