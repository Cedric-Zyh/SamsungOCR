"""Readable, immutable standard images and compatibility for saved image URLs."""
import filecmp
import json
import logging
from pathlib import Path
import re
import shutil
import sqlite3
import unicodedata
from urllib.parse import unquote, urlsplit


_RANDOM_PNG = re.compile(r"[0-9a-f]{32}\.png", re.IGNORECASE)
_INVALID = re.compile(r'[<>:"/\\|?*]')
_DEVICE = re.compile(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', re.IGNORECASE)


def _filename_stem(requirement):
    value = unicodedata.normalize('NFC', str(requirement or ''))
    value = ''.join(' ' if c.isspace() else c for c in value
                    if not unicodedata.category(c).startswith('C') or c.isspace())
    value = ' '.join(_INVALID.sub('_', value).split()).strip(' .') or '标准章'
    return '_' + value if _DEVICE.fullmatch(value) else value


def _bounded_name(stem, suffix):
    # Leave room under both common filesystem byte and UTF-16 name limits.
    while (len((stem + suffix).encode('utf-8')) > 220
           or len((stem + suffix).encode('utf-16-le')) // 2 > 220):
        stem = stem[:-1]
    return stem.rstrip(' .') + suffix


def reserve_standard_image(directory, requirement, *, revision=1, reserved_names=()):
    """Reserve a new PNG without ever overwriting another revision or file."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stem = _filename_stem(requirement)
    version = f' - v{revision}' if revision > 1 else ''
    occupied = {unicodedata.normalize('NFC', name).casefold()
                for name in (*reserved_names, *(p.name for p in directory.iterdir()))}
    counter = 1
    while True:
        suffix = version + (f' - {counter}' if counter > 1 else '') + '.png'
        name = _bounded_name(stem, suffix)
        if unicodedata.normalize('NFC', name).casefold() not in occupied:
            path = directory / name
            try:
                with path.open('xb'):
                    pass
                return path
            except FileExistsError:
                pass
        counter += 1


def _local_image(directory, name):
    if not isinstance(name, str) or not name or '/' in name or '\\' in name or name in {'.', '..'}:
        return None
    path = directory / name
    return path if path.is_file() and not path.is_symlink() else None


def _historical_standards(connection, missing):
    """Find only named legacy images; saved OCR and audit payloads stay intact."""
    found = {}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in [('results', ('result_json', 'original_result_json')),
                           ('review_history', ('before_json', 'after_json'))]:
        if table not in tables:
            continue
        available = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        for column in columns:
            if column not in available:
                continue
            for row in connection.execute(f"""SELECT json_extract({column}, '$.seal_standard_check.standard')
                    FROM {table} WHERE json_valid({column}) AND {column} LIKE '%/files/seal-standards/%'"""):
                if not row[0]:
                    continue
                try:
                    standard = json.loads(row[0])
                except (ValueError, TypeError):
                    continue
                if not isinstance(standard, dict):
                    continue
                url = standard.get('image_url') or ''
                if not isinstance(url, str):
                    continue
                name = unquote(urlsplit(url).path.removeprefix('/files/seal-standards/'))
                if name in missing and standard.get('requirement'):
                    found[name] = standard
                    if missing <= found.keys():
                        return found
    return found


def migrate_standard_images(database, *, history_databases=()):
    """Copy first, commit references/aliases, then remove identical old files.

    A failed transaction retains all old images. A restart after commit finishes
    cleanup using the aliases, without changing revisions or audit snapshots.
    """
    directory = database.path.parent / 'seal-standards'
    if not directory.is_dir():
        return 0
    created = []
    try:
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute('SELECT id,requirement,image_name,revision FROM seal_standards ORDER BY id').fetchall()
            aliases = dict(connection.execute('SELECT old_name,image_name FROM seal_standard_image_aliases'))
            current_names = {row['image_name'] for row in rows}
            legacy = {p.name for p in directory.iterdir() if _RANDOM_PNG.fullmatch(p.name)
                      and p.is_file() and not p.is_symlink()} - current_names - aliases.keys()
            historical = _historical_standards(connection, legacy) if legacy else {}
            for path in history_databases:
                if not legacy - historical.keys():
                    break
                if Path(path).is_file() and Path(path).resolve() != database.path.resolve():
                    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as history:
                        historical.update(_historical_standards(history, legacy - historical.keys()))
            entries = [(row['image_name'], dict(row), row['id']) for row in rows
                       if _RANDOM_PNG.fullmatch(row['image_name'])]
            entries.extend((name, standard, None) for name, standard in sorted(historical.items()))
            for old_name, standard, standard_id in entries:
                source = _local_image(directory, old_name)
                if source is None:
                    continue
                revision = standard.get('revision')
                revision = revision if type(revision) is int and revision > 0 else 1
                target = reserve_standard_image(directory, standard['requirement'],
                    revision=revision, reserved_names=aliases.keys())
                created.append(target)
                shutil.copy2(source, target)
                connection.execute('INSERT OR IGNORE INTO seal_standard_image_aliases(old_name,image_name) VALUES(?,?)',
                                   (old_name, target.name))
                aliases.setdefault(old_name, target.name)
                if standard_id is not None:
                    connection.execute('UPDATE seal_standards SET image_name=? WHERE id=?', (target.name, standard_id))
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    for old_name, image_name in aliases.items():
        try:
            source, target = _local_image(directory, old_name), _local_image(directory, image_name)
            if source and target and filecmp.cmp(source, target, shallow=False):
                source.unlink()
        except OSError as exc:
            # New references and compatibility links are already committed.
            # Leave this redundant file for cleanup on the next startup.
            logging.getLogger(__name__).warning('标准章旧文件暂未清理 %s: %s', old_name, exc)
    return len(created)
