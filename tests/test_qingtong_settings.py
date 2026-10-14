from receipt_ocr.persistence.database import Database
from receipt_ocr.web import application as web


def test_qingtong_save_clear_and_restart(monkeypatch, tmp_path):
    db = Database(tmp_path / 'settings.db')
    db.initialize()
    monkeypatch.setattr(web, 'database', db)
    monkeypatch.setattr(web, 'job_store', web.job_store)
    monkeypatch.setattr(web.analyzer.seal_api, 'api_key', '')
    client = web.app.test_client()
    assert client.get('/api/settings/qingtong').json == {'configured': False}
    response = client.patch('/api/settings/qingtong', json={'api_key': 'test-secret'})
    assert response.json == {'configured': True}
    assert 'test-secret' not in response.get_data(as_text=True)
    assert web.analyzer.seal_api.api_key == 'test-secret'
    page = client.get('/').get_data(as_text=True)
    assert 'id="qingtong-form"' in page
    assert 'id="qingtong-api-key" type="password"' in page
    assert 'test-secret' not in page
    # Reinitialize against the same database, as on application restart.
    web.analyzer.seal_api.api_key = ''
    web.initialize(start_worker=False)
    assert web.analyzer.seal_api.api_key == 'test-secret'
    assert client.patch('/api/settings/qingtong', json={'api_key': ''}).json == {'configured': False}
    web.initialize(start_worker=False)
    assert not web.analyzer.seal_api.enabled


def test_qingtong_rejects_invalid_and_cross_origin_updates(monkeypatch, tmp_path):
    db = Database(tmp_path / 'settings.db')
    db.initialize()
    monkeypatch.setattr(web, 'database', db)
    monkeypatch.setattr(web.analyzer.seal_api, 'api_key', 'existing')
    client = web.app.test_client()
    for payload in ({}, {'api_key': 12}, {'api_key': 'two words'}):
        assert client.patch('/api/settings/qingtong', json=payload).status_code == 400
    assert client.patch('/api/settings/qingtong', json={'api_key': 'other'},
                        headers={'Origin': 'https://other.example'}).status_code == 403
    assert web.analyzer.seal_api.api_key == 'existing'
