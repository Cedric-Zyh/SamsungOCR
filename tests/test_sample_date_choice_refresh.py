"""Saved sample date summaries follow the same choice as new recognition."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.samples.preservation import refresh_sample_date_choices
from test_sample_center import samples
from test_sample_preserved_footer import applied_preserved_return


def saved_date_conflict(samples):
    service, item, result = applied_preserved_return(samples)
    far = {'required': '2025-02-22', 'actual': '2025-02-02', 'status': '不匹配',
           'reliable': False, 'source': '日期区域识别', 'message': '早 20 天'}
    near = {'required': '2025-02-22', 'actual': '2025-02-21', 'status': '不匹配',
            'reliable': True, 'source': '单证通', 'message': '早 1 天'}
    result['date_check'] = far
    result['fields'].update(要求到货='2025-02-22', 签收日期='2025-02-02')
    result['recognition_config'].setdefault('acceptance', {})['date_match_mode'] = 'any'
    result['recognition_variants']['date'] = [
        {'method': method, 'value': [check['actual'], check['status']],
         'details': {'date_check': deepcopy(check)}}
        for method, check in [('paddle_v6', far), ('danzhengtong', near)]]
    with service.database.connect() as con:
        payload = json.dumps(result, ensure_ascii=False)
        con.execute('UPDATE results SET result_json=?,original_result_json=? WHERE id=?',
                    (payload, payload, item['result_id']))
    return service, item, result


def test_saved_date_choice_updates_detail_list_statistics_and_audit_without_provider_calls(samples):
    _, client = samples
    service, item, before = saved_date_conflict(samples)
    remote = service.store.remote_detail(item['job_id'])
    with service.database.connect() as con:
        original = con.execute('SELECT original_result_json FROM results WHERE id=?',
                               (item['result_id'],)).fetchone()[0]
    assert refresh_sample_date_choices(service.store) == 1
    after = service.database.get_result(item['result_id'])
    assert after['date_check'] == before['recognition_variants']['date'][1]['details']['date_check']
    assert after['fields']['签收日期'] == '2025-02-21'
    for key in ('recognition_variants', 'processing_artifacts', 'review_issues', 'overall', 'final_result'):
        assert after[key] == before[key]
    assert service.store.remote_detail(item['job_id']) == remote
    assert client.get(f"/api/samples/results/{item['result_id']}").json['date_check']['actual'] == '2025-02-21'
    listing = next(row for row in client.get('/api/samples').json['items'] if row['filename'] == 'one.png')
    assert listing['result_filters']['date']['value'] == '2025-02-21'
    stats = next(row for row in client.get('/api/samples/statistics').json['items'] if row['filename'] == 'one.png')
    assert stats['date']['value'] == '2025-02-21'
    history = service.database.history(item['result_id'])
    assert history[0]['action'] == '样单日期汇总更新'
    assert json.loads(history[0]['before_json'])['date_check']['actual'] == '2025-02-02'
    with service.database.connect() as con:
        assert con.execute('SELECT original_result_json FROM results WHERE id=?',
                           (item['result_id'],)).fetchone()[0] == original
    assert refresh_sample_date_choices(service.store) == 0
    assert service.database.history(item['result_id']) == history


@pytest.mark.parametrize('guard', ['human_date', 'human_history', 'waiting', 'newer_job'])
def test_saved_date_choice_preserves_human_and_active_or_superseded_samples(samples, guard):
    service, item, result = saved_date_conflict(samples)
    with service.database.connect() as con:
        if guard == 'human_date':
            result['date_check']['source'] = '人工复核'
            con.execute('UPDATE results SET result_json=? WHERE id=?',
                        (json.dumps(result, ensure_ascii=False), item['result_id']))
        elif guard == 'waiting':
            con.execute("UPDATE danzhengtong_jobs SET status='waiting' WHERE job_id=?", (item['job_id'],))
        elif guard == 'newer_job':
            con.execute('''INSERT INTO recognition_jobs
                (id,task_id,client_key,filename,status,payload_json,created_at)
                VALUES('newer',?,'newer','one.png','cancelled','{}','now')''',
                (service.store.get(item['job_id'])['task_id'],))
    if guard == 'human_history':
        service.database.review_result(item['result_id'], result=result, review_status='待复核',
                                       final_result='需人工复核', note='', action='人工修改')
    before = service.database.get_result(item['result_id'])
    assert refresh_sample_date_choices(service.store) == 0
    assert service.database.get_result(item['result_id']) == before
