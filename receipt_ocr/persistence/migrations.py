"""Ordered component migrations, committed with the caller's transaction.

Migration callbacks must not commit or use sqlite3.executescript: that API
implicitly commits pending work. Use execute_script for multi-statement SQL.
"""
import sqlite3

from .database_clock import now_iso


def execute_script(connection, script):
    """Execute trusted schema SQL without sqlite3.executescript's implicit commit."""
    statement = ''
    for character in script:
        statement += character
        if character == ';' and sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ''
    if statement.strip():
        connection.execute(statement)


def run_migrations(connection, component, steps):
    """Serialize upgrades and record each successful step in the same transaction."""
    steps = tuple(steps)
    if [version for version, _, _ in steps] != list(range(1, len(steps) + 1)):
        raise ValueError('Migration versions must be consecutive, starting at 1')
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    connection.execute('SAVEPOINT component_migration')
    try:
        connection.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            component TEXT NOT NULL, version INTEGER NOT NULL,
            name TEXT NOT NULL, applied_at TEXT NOT NULL,
            PRIMARY KEY(component, version)
        )''')
        applied = dict(connection.execute(
            'SELECT version,name FROM schema_migrations WHERE component=?', (component,)))
        expected = {version: name for version, name, _ in steps}
        if any(expected.get(version) != name for version, name in applied.items()):
            raise RuntimeError(f'数据库迁移版本与当前程序不兼容：{component}')
        if sorted(applied) != list(range(1, len(applied) + 1)):
            raise RuntimeError(f'数据库迁移记录不连续：{component}')
        for version, name, migrate in steps:
            if version in applied:
                continue
            migrate(connection)
            connection.execute('''INSERT INTO schema_migrations
                (component,version,name,applied_at) VALUES(?,?,?,?)''',
                (component, version, name, now_iso()))
        connection.execute('RELEASE component_migration')
    except BaseException:
        connection.execute('ROLLBACK TO component_migration')
        connection.execute('RELEASE component_migration')
        raise
