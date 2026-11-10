from pathlib import Path
import sqlite3
from urllib.parse import unquote

import pytest

from receipt_ocr.persistence.database import Database
from receipt_ocr.persistence.seal_standard_images import migrate_standard_images, reserve_standard_image
from test_seal_standards import app_store, artifact, register, REQUIREMENT


def save(db, name, requirement=REQUIREMENT, revision=0):
    return db.save_seal_standard(dict(requirement=requirement, image_name=name,
        recognized=requirement, shape='round', color='red', source_result_id=0,
        source_filename='来源.png', candidate_id='test'), expected_revision=revision)


def test_registration_and_replacement_use_requirement_and_keep_previous_png(app_store):
    db, client, add, _ = app_store
    result_id = add(artifact())
    first = register(client, result_id).get_json()['standard']
    assert unquote(first['image_url'].rsplit('/', 1)[-1]) == REQUIREMENT + '.png'
    first_bytes = client.get(first['image_url']).data
    replaced = register(client, result_id, revision=1, rotation=90).get_json()['standard']
    assert unquote(replaced['image_url'].rsplit('/', 1)[-1]) == REQUIREMENT + ' - v2.png'
    assert client.get(first['image_url']).data == first_bytes
    assert client.get(replaced['image_url']).status_code == 200
    assert len(list((db.path.parent / 'seal-standards').glob('*.png'))) == 2


def test_file_url_encodes_fragment_percent_and_unicode(app_store):
    db, client, add, _ = app_store
    requirement = '甲公司#1%23收货章'
    saved = register(client, add(artifact(text=requirement), requirement=requirement)).get_json()['standard']
    assert '%23' in saved['image_url'] and '%25' in saved['image_url']
    assert unquote(saved['image_url'].rsplit('/', 1)[-1]) == requirement + '.png'
    assert client.get(saved['image_url']).status_code == 200


@pytest.mark.parametrize('requirement', ['../甲公司/库房\\章:*?<>|"\n', 'CON', 'aux.txt', ' . ', '字' * 1000, '😀' * 1000])
def test_names_are_safe_and_fit_filesystem_limits(tmp_path, requirement):
    path = reserve_standard_image(tmp_path, requirement)
    assert path.parent == tmp_path and path.is_file()
    assert not any(c in path.name for c in '<>:"/\\|?*\n')
    assert len(path.name.encode('utf-8')) <= 220
    assert len(path.name.encode('utf-16-le')) // 2 <= 220
    assert not path.name.startswith('.')
    assert path.suffix == '.png'


def test_sanitized_case_and_long_name_collisions_preserve_existing_files(tmp_path):
    first = reserve_standard_image(tmp_path, 'ABC/公章')
    first.write_bytes(b'original')
    next_path = reserve_standard_image(tmp_path, 'abc:公章')
    assert next_path.name == 'abc_公章 - 2.png'
    assert first.read_bytes() == b'original'
    long_first = reserve_standard_image(tmp_path, '字' * 1000 + '甲')
    long_next = reserve_standard_image(tmp_path, '字' * 1000 + '乙')
    assert long_first != long_next
    assert reserve_standard_image(tmp_path, '保留名', reserved_names=['保留名.png']).name == '保留名 - 2.png'


def test_current_and_historical_migration_preserves_metadata_snapshots_and_old_urls(app_store, tmp_path):
    db, client, add, _ = app_store
    directory = db.path.parent / 'seal-standards'
    directory.mkdir()
    current_name, old_name = 'a' * 32 + '.png', 'b' * 32 + '.png'
    (directory / old_name).write_bytes(b'old image')
    (directory / current_name).write_bytes(b'current image')
    old = save(db, old_name)
    current = save(db, current_name, revision=1)
    history_db = Database(tmp_path / 'samples' / 'results.db')
    history_db.initialize()
    snapshot = dict(fields={'签章要求': REQUIREMENT}, seal_standard_check={'standard': old})
    history_db.insert_result(filename='历史样单.png', stored_name='test', preview_name='', task_id='', result=snapshot)
    with history_db.connect() as con:
        before = con.execute('SELECT result_json,original_result_json FROM results').fetchone()
        before = tuple(before)
    assert migrate_standard_images(db, history_databases=[history_db.path]) == 2
    saved = db.get_seal_standard(REQUIREMENT)
    assert unquote(saved['image_url'].rsplit('/', 1)[-1]) == REQUIREMENT + ' - v2.png'
    assert {k: v for k, v in saved.items() if k != 'image_url'} == {k: v for k, v in current.items() if k != 'image_url'}
    assert client.get(current['image_url']).data == b'current image'
    assert client.get(old['image_url']).data == b'old image'
    assert (directory / (REQUIREMENT + '.png')).read_bytes() == b'old image'
    assert not (directory / current_name).exists() and not (directory / old_name).exists()
    with history_db.connect() as con:
        assert tuple(con.execute('SELECT result_json,original_result_json FROM results').fetchone()) == before
    assert migrate_standard_images(db, history_databases=[history_db.path]) == 0
    # Simulate a process stopping after committing references but before cleanup.
    (directory / current_name).write_bytes(b'current image')
    assert migrate_standard_images(db) == 0
    assert not (directory / current_name).exists()


def test_migration_rollback_keeps_old_files_and_references(tmp_path):
    db = Database(tmp_path / 'results.db')
    db.initialize()
    directory = tmp_path / 'seal-standards'
    directory.mkdir()
    for index, requirement in enumerate(['甲公司', '乙公司']):
        name = str(index) * 32 + '.png'
        (directory / name).write_bytes(requirement.encode())
        save(db, name, requirement)
    before = db.list_seal_standards()
    with db.connect() as con:
        con.execute("""CREATE TRIGGER fail_second_alias BEFORE INSERT ON seal_standard_image_aliases
            WHEN NEW.old_name LIKE '1%' BEGIN SELECT RAISE(ABORT, '模拟失败'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        migrate_standard_images(db)
    assert db.list_seal_standards() == before
    assert {p.name for p in directory.iterdir()} == {'0' * 32 + '.png', '1' * 32 + '.png'}
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM seal_standard_image_aliases').fetchone()[0] == 0


def test_missing_unknown_and_symlink_images_are_left_untouched(tmp_path):
    db = Database(tmp_path / 'results.db')
    db.initialize()
    directory = tmp_path / 'seal-standards'
    directory.mkdir()
    external = tmp_path / 'external.png'
    external.write_bytes(b'original')
    linked = directory / ('c' * 32 + '.png')
    linked.symlink_to(external)
    save(db, linked.name)
    missing = 'd' * 32 + '.png'
    save(db, missing, '缺失章')
    unknown = directory / ('e' * 32 + '.png')
    unknown.write_bytes(b'unknown')
    before = db.list_seal_standards()
    assert migrate_standard_images(db) == 0
    assert db.list_seal_standards() == before
    assert linked.is_symlink() and external.read_bytes() == b'original'
    assert unknown.read_bytes() == b'unknown'


def test_cleanup_failure_keeps_alias_usable_and_retries_later(app_store, monkeypatch):
    db, client, _, _ = app_store
    directory = db.path.parent / 'seal-standards'
    directory.mkdir()
    source = directory / ('f' * 32 + '.png')
    source.write_bytes(b'unchanged image')
    old = save(db, source.name)
    unlink = Path.unlink
    with monkeypatch.context() as patch:
        def fail_old(path, *args, **kwargs):
            if path == source:
                raise PermissionError('模拟暂时占用')
            return unlink(path, *args, **kwargs)
        patch.setattr(Path, 'unlink', fail_old)
        assert migrate_standard_images(db) == 1
    assert source.exists()
    assert client.get(old['image_url']).data == b'unchanged image'
    assert migrate_standard_images(db) == 0
    assert not source.exists()


def test_shared_legacy_image_gets_a_readable_copy_for_each_requirement(app_store):
    db, client, _, _ = app_store
    directory = db.path.parent / 'seal-standards'
    directory.mkdir()
    old_name = '9' * 32 + '.png'
    (directory / old_name).write_bytes(b'shared legacy image')
    first = save(db, old_name, '甲公司公章')
    save(db, old_name, '乙公司公章')
    assert migrate_standard_images(db) == 2
    assert {p.name for p in directory.iterdir()} == {'甲公司公章.png', '乙公司公章.png'}
    assert client.get(first['image_url']).data == b'shared legacy image'
    assert db.get_seal_standard('甲公司公章')['image_url'] != db.get_seal_standard('乙公司公章')['image_url']
