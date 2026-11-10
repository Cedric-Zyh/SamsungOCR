"""Result write use cases retain their distinct baseline and audit guarantees."""
import json

import pytest

from receipt_ocr.persistence.database import Database
from test_database import sample_result


@pytest.fixture
def receipt(tmp_path):
    database = Database(tmp_path / 'writes.db')
    database.initialize()
    database.create_task('task', '写入测试', 1)
    result_id = database.result_writes.create_recognition(filename='receipt.jpg', stored_name='receipt.jpg',
        preview_name='', task_id='task', result=sample_result('receipt.jpg'))
    database.update_task('task', success=True, pending_review=True)
    return database, result_id


def saved_row(database, result_id):
    with database.connect() as con:
        return dict(con.execute('SELECT * FROM results WHERE id=?', (result_id,)).fetchone())


def test_human_review_and_retry_both_preserve_the_first_machine_baseline(receipt):
    database, result_id = receipt
    original = saved_row(database, result_id)['original_result_json']
    reviewed = sample_result('receipt.jpg')
    reviewed['fields']['客户名称'] = '人工确认客户'
    database.result_writes.save_review(result_id, result=reviewed, review_status='确认通过',
        final_result='通过', note='人工核对', action='人工复核', error_type='字段识别错误')
    assert saved_row(database, result_id)['original_result_json'] == original
    assert database.get_task('task')['pending_review'] == 0

    retried = sample_result('receipt.jpg')
    retried['fields']['客户名称'] = '重新识别客户'
    database.result_writes.replace_recognition(result_id, retried, 'retry.jpg')
    current = saved_row(database, result_id)
    assert current['original_result_json'] == original
    assert current['attempt'] == 2 and current['review_status'] == '待复核'
    assert current['human_note'] == '人工核对'
    assert database.get_task('task')['pending_review'] == 1
    assert [row['action'] for row in database.history(result_id)] == ['重新识别', '人工复核']


def test_remote_completion_updates_machine_baseline_without_creating_a_retry(receipt):
    database, result_id = receipt
    combined = sample_result('receipt.jpg')
    combined['fields']['客户名称'] = '远程完成后的机器客户'
    database.result_writes.apply_remote_result(result_id, combined)
    row = saved_row(database, result_id)
    assert json.loads(row['original_result_json'])['fields']['客户名称'] == '远程完成后的机器客户'
    assert row['attempt'] == 1
    assert row['error_type'] == row['error_message'] == ''
    assert [item['action'] for item in database.history(result_id)] == ['取回单证通结果']


def test_retained_provider_restore_does_not_count_as_a_new_recognition_or_review(receipt):
    database, result_id = receipt
    before = saved_row(database, result_id)
    retained = sample_result('receipt.jpg')
    retained['recognition_variants'] = {'seal': [{'method': 'paddle_v6', 'retained': True}]}
    database.result_writes.restore_retained_result(result_id, retained, 'retained.jpg')
    row = saved_row(database, result_id)
    assert row['preview_name'] == 'retained.jpg'
    assert json.loads(row['result_json'])['recognition_variants'] == retained['recognition_variants']
    for key in ('original_result_json', 'created_at', 'updated_at', 'attempt', 'human_note', 'error_type'):
        assert row[key] == before[key]
    assert database.history(result_id) == []


def test_historical_repair_preserves_human_metadata_and_only_explicitly_repairs_baseline(receipt):
    database, result_id = receipt
    database.result_writes.save_review(result_id, result=sample_result(), review_status='确认不通过',
        final_result='不通过', note='不要覆盖人工备注', action='人工复核', error_type='印章问题')
    before = saved_row(database, result_id)
    correction = database.get_result(result_id)
    correction['fields']['附加说明'] = '修复非人工证据'
    database.result_writes.repair_saved_result(result_id, result=correction,
        review_status='确认不通过', final_result='不通过', action='补充历史证据')
    row = saved_row(database, result_id)
    assert row['human_note'] == before['human_note']
    assert row['review_status'] == before['review_status']
    assert row['final_result'] == before['final_result']
    assert row['error_type'] == before['error_type']
    assert row['original_result_json'] == before['original_result_json']
    repaired_original = json.loads(before['original_result_json'])
    repaired_original['signature_footer'] = {'present': True}
    database.result_writes.repair_saved_result(result_id, result=correction,
        review_status='确认不通过', final_result='不通过', action='修复机器页脚证据',
        original_result=repaired_original)
    assert json.loads(saved_row(database, result_id)['original_result_json']) == repaired_original


def test_failure_during_shared_finalization_rolls_back_result_audit_and_counts(receipt, monkeypatch):
    database, result_id = receipt
    before = saved_row(database, result_id)
    original = database.reconcile_task_pages

    def fail_after_page_updates(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('page association persistence failed')

    monkeypatch.setattr(database, 'reconcile_task_pages', fail_after_page_updates)
    with pytest.raises(RuntimeError, match='page association'):
        database.result_writes.save_review(result_id, result=sample_result(), review_status='确认通过',
            final_result='通过', note='本次不得提交', action='人工复核')
    assert saved_row(database, result_id) == before
    assert database.history(result_id) == []
    assert database.get_task('task')['pending_review'] == 1


def test_repair_joins_caller_transaction_without_committing_its_baseline_early(receipt):
    database, result_id = receipt
    before = saved_row(database, result_id)
    with pytest.raises(RuntimeError, match='snapshot failure'):
        with database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            corrected = sample_result()
            corrected['fields']['客户名称'] = '修复值'
            database.result_writes.repair_saved_result(result_id, result=corrected,
                review_status='待复核', final_result='需人工复核', action='修复历史',
                original_result=corrected, connection=con)
            raise RuntimeError('snapshot failure')
    assert saved_row(database, result_id) == before
    assert database.history(result_id) == []
