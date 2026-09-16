import pytest
from receipt_ocr.database import Database
from tests.test_database import sample_result


def test_delete_removes_repeats_from_records_dates_and_export_source(tmp_path):
    db = Database(tmp_path / 'db.sqlite'); db.initialize()
    ids = []
    for day in ['2026-09-08','2026-09-09','2026-09-09']:
        item=sample_result('same.jpg'); item['created_at']=day+'T12:00:00+08:00'
        ids.append(db.insert_result(filename='same.jpg',stored_name='same.jpg',preview_name='',task_id='',result=item))
    affected = db.delete_results([ids[-1]])
    assert affected == ids[1:]
    assert not db.list_results(filters={'import_date':'2026-09-09'},latest_by_filename=True)
    assert db.import_date_counts('2026-09') == {'2026-09-08':1}
    assert [r['id'] for r in db.list_original_results()] == ids[:1]
    with pytest.raises(KeyError): db.get_result(ids[-1])
    with db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM results').fetchone()[0] == 1


def test_delete_api_validation_and_no_restore(tmp_path,monkeypatch):
    import app as web
    db=Database(tmp_path/'db.sqlite');db.initialize();monkeypatch.setattr(web,'database',db)
    item=sample_result();rid=db.insert_result(filename=item['filename'],stored_name='x',preview_name='',task_id='',result=item)
    client=web.app.test_client()
    assert client.post('/api/results/delete',json={'ids':[]}).status_code == 400
    assert client.post('/api/results/delete',json={'ids':[rid]}).status_code == 200
    assert client.get('/api/trash').status_code == 404
    assert client.post('/api/results/delete',json={'ids':[rid]}).status_code == 404


def test_delete_does_not_follow_obsolete_group_after_reclassification(tmp_path):
    from tests.test_database import add_two_page_receipt
    database = Database(tmp_path / "delete.db")
    database.initialize()
    cover_id, continuation_id = add_two_page_receipt(database)
    current = database.get_result(continuation_id)
    current["document_type"] = {"type": "receipt"}
    database.replace_after_retry(continuation_id, current, "")

    assert database.delete_results([cover_id]) == [cover_id]
    assert database.get_result(continuation_id)["document_type"]["type"] == "receipt"


def test_delete_repairs_legacy_stale_associations_before_expanding_selection(tmp_path):
    import json
    from tests.test_database import add_two_page_receipt
    database = Database(tmp_path / "legacy-delete.db")
    database.initialize()
    cover_id, continuation_id = add_two_page_receipt(database)
    with database.connect() as connection:
        row = connection.execute("SELECT result_json FROM results WHERE id=?", (continuation_id,)).fetchone()
        payload = json.loads(row["result_json"])
        payload["document_type"] = {"type": "receipt"}
        # Older versions changed the recognized type but retained group columns.
        connection.execute("UPDATE results SET result_json=? WHERE id=?", (json.dumps(payload), continuation_id))

    assert database.delete_results([cover_id]) == [cover_id]
    assert database.get_result(continuation_id)["parent_result_id"] == 0
