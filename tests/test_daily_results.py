from receipt_ocr.web import application as app_module
from receipt_ocr.persistence.database import Database


def test_daily_results_combines_tasks_and_scopes_before_dedup(bind_application_runtime, tmp_path, monkeypatch):
    db = Database(tmp_path / 'daily.db')
    db.initialize()
    bind_application_runtime(database=db)
    for filename, day, task, status in [
        ('a.jpg', '2026-09-08', 'first', '待复核'),
        ('b.jpg', '2026-09-08', 'second', '待复核'),
        ('a.jpg', '2026-09-08', 'third', '确认通过'),
        ('a.jpg', '2026-09-09', 'fourth', '待复核'),
    ]:
        db.insert_result(filename=filename, stored_name=filename, preview_name='', task_id=task,
                       result={'filename':filename, 'created_at':day+'T10:00:00', 'review_status':status, 'overall':'通过'})
    client = app_module.app.test_client()
    response = client.get('/api/daily-results?import_date=2026-09-08')
    assert response.status_code == 200
    rows = response.get_json()
    assert len(rows) == 2
    assert {r['filename'] for r in rows} == {'a.jpg', 'b.jpg'}
    assert next(r for r in rows if r['filename'] == 'a.jpg')['review_status'] == '确认通过'
    assert client.get('/api/daily-results?import_date=2026-09-07').get_json() == []
    assert client.get('/api/daily-results').status_code == 400
    assert client.get('/api/daily-results?import_date=2026-02-30').status_code == 400


def test_workbench_includes_cross_day_retry_results_by_id(bind_application_runtime, tmp_path, monkeypatch):
    from receipt_ocr.jobs.store import JobStore
    db = Database(tmp_path / 'retry.db')
    db.initialize()
    store = JobStore(db)
    store.initialize()
    bind_application_runtime(database=db, job_store=store)
    old_id = db.insert_result(filename='same.jpg', stored_name='old.jpg', preview_name='', task_id='',
        result={'created_at': '2026-09-08T10:00:00', 'fields': {'客户名称': '原回单客户'},
                'date_check': {'status': '未识别', 'actual': '', 'required': '2025-03-31', 'reliable': False},
                'seal_check': {'status': '匹配', 'reliable': True}, 'overall': '需人工复核'})
    new_id = db.insert_result(filename='same.jpg', stored_name='new.jpg', preview_name='', task_id='',
        result={'created_at': '2026-09-09T10:00:00', 'fields': {'客户名称': '另一张同名回单'}, 'overall': '通过'})
    batch = store.create_batch('retry', '跨天重试', [{'filename': 'same.jpg'}],
                               {'ocr_backend': 'vision', 'seal_recognition_mode': 'local'})
    with db.connect() as con:
        con.execute("UPDATE batch_tasks SET created_at='2026-09-09T11:00:00' WHERE id='retry'")
        con.execute("UPDATE recognition_jobs SET status='succeeded',result_id=?,target_result_id=? WHERE id=?",
                    (old_id, old_id, batch['items'][0]['id']))
    client = app_module.app.test_client()
    assert [r['id'] for r in client.get('/api/daily-results?import_date=2026-09-09').get_json()] == [new_id]
    url = '/api/daily-results?import_date=2026-09-09&include_queue=1'
    rows = client.get(url).get_json()
    assert {r['id'] for r in rows} == {old_id, new_id}
    old = next(r for r in rows if r['id'] == old_id)
    detail = client.get(f'/api/results/{old_id}').get_json()
    assert old['fields'] == detail['fields'] == {'客户名称': '原回单客户'}
    assert old['seal_check'] == detail['seal_check'] == {'status': '匹配', 'reliable': True}
    assert old['date_check'] == detail['date_check']
    # Deleted retry targets do not reappear on the workbench.
    with db.connect() as con:
        con.execute("UPDATE results SET deleted_at='2026-09-09T12:00:00' WHERE id=?", (old_id,))
    assert [r['id'] for r in client.get(url).get_json()] == [new_id]


def test_header_filters_apply_to_list(bind_application_runtime, tmp_path, monkeypatch):
    db = Database(tmp_path / 'filter-scope.db')
    db.initialize()
    bind_application_runtime(database=db)
    for filename, reliable in [('7123.jpg', True), ('7281.jpg', False), ('8172.jpg', True)]:
        db.insert_result(filename=filename, stored_name=filename, preview_name='', task_id='', result={
            'created_at': '2026-09-10T10:00:00', 'overall': '需人工复核',
            'internal_fields': {'客户订单号': '7654' if filename == '8172.jpg' else ''},
            'date_check': {'status': '未识别'}, 'seal_check': {'status': '匹配', 'reliable': reliable}})
    query = '?search_prefix=7&seal_status=匹配&date_status=未识别&import_date=2026-09-10'
    client = app_module.app.test_client()
    listed = client.get('/api/results' + query).get_json()
    assert [r['filename'] for r in listed] == ['8172.jpg', '7123.jpg']


def test_pending_seal_filter_combines_review_states_and_preserves_details(bind_application_runtime, tmp_path, monkeypatch):
    db = Database(tmp_path / 'pending-seals.db')
    db.initialize()
    bind_application_runtime(database=db)
    checks = [
        ('unreliable.jpg', '匹配', False),
        ('pending.jpg', '匹配待确认', False),
        ('review.jpg', '需人工复核', False),
        ('matched.jpg', '匹配', True),
        ('partial.jpg', '部分匹配', False),
        ('mismatch.jpg', '不匹配', True),
        ('unrecognized.jpg', '未识别', False),
        ('unexecuted.jpg', '未执行', False),
    ]
    ids = {}
    for filename, status, reliable in checks:
        ids[filename] = db.insert_result(filename=filename, stored_name=filename, preview_name='', task_id='', result={
            'created_at': '2026-09-10T10:00:00', 'overall': '需人工复核',
            'date_check': {'status': '匹配', 'reliable': False},
            'seal_check': {'status': status, 'reliable': reliable, 'message': f'{filename} 的原始判断原因'}})
    client = app_module.app.test_client()
    query = {'seal_status': '待确认', 'date_status': '匹配待确认', 'import_date': '2026-09-10',
             'page': 1, 'page_size': 2, 'include_status_counts': 1}
    first = client.get('/api/results', query_string=query).get_json()
    second = client.get('/api/results', query_string={**query, 'page': 2}).get_json()
    assert first['total'] == second['total'] == first['status_counts'][''] == 3
    assert {row['filename'] for row in first['items'] + second['items']} == {
        'unreliable.jpg', 'pending.jpg', 'review.jpg'}
    for filename, status, reliable in checks[:3]:
        detail = client.get(f'/api/results/{ids[filename]}').get_json()
        assert detail['seal_check'] == {'status': status, 'reliable': reliable,
                                        'message': f'{filename} 的原始判断原因'}
    # Existing narrower filters and the date filter retain their meaning.
    assert len(client.get('/api/results', query_string={'seal_status': '匹配待确认'}).get_json()) == 2
    assert len(client.get('/api/results', query_string={'seal_status': '需人工复核'}).get_json()) == 1
    assert len(client.get('/api/results', query_string={'date_status': '匹配待确认'}).get_json()) == 8
