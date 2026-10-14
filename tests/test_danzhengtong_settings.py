import io

import pytest
from receipt_ocr import danzhengtong as dzt
from receipt_ocr.persistence.database import Database
from receipt_ocr.web import application as web
from receipt_ocr.web import remote_settings


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = Database(tmp_path / 'settings.db')
    db.initialize()
    monkeypatch.setattr(web, 'database', db)
    monkeypatch.setattr(dzt, '_software_settings', dzt.Settings())
    return web.app.test_client()


def credentials():
    return {'app_id': 'id', 'app_key': 'secret-key', 'app_secret': 'secret-app',
            'key_id': 'secret-id', 'callback_url': 'https://example.com/callback'}


def test_save_mask_preserve_restart_and_clear(client):
    assert client.get('/api/settings/danzhengtong').json['status'] == '未配置'
    assert client.patch('/api/settings/danzhengtong', json={'app_id': 'id'}).json['status'] == '配置不完整'
    result = client.patch('/api/settings/danzhengtong', json=credentials())
    assert result.json['status'] == '已配置'
    for secret in ('secret-key', 'secret-app', 'secret-id'):
        assert secret not in result.get_data(as_text=True)
        assert secret not in client.get('/').get_data(as_text=True)
    old_client = dzt.Client()
    assert client.patch('/api/settings/danzhengtong', json={'app_secret': '', 'app_key': 'new-key'}).status_code == 200
    assert dzt.Settings.load().app_secret == 'secret-app'
    assert old_client.settings.app_key == 'secret-key'
    assert dzt.Client().settings.app_key == 'new-key'
    dzt.set_software_settings(None)
    remote_settings.restore(web.database)
    assert dzt.Settings.load().app_key == 'new-key'
    assert client.patch('/api/settings/danzhengtong', json={'clear': True}).json['status'] == '未配置'
    remote_settings.restore(web.database)
    assert not dzt.Settings.load().app_key


def test_validation(client):
    for payload in ({'poll_timeout_seconds': 0}, {'poll_timeout_seconds': 61},
                    {'poll_timeout_seconds': True}, {'base_url': 'file:///tmp/a'},
                    {'base_url': 'https://['}, {'base_url': 'https://example.com:invalid'},
                    {'upload_url': 'https://user:pass@example.com'}, {'app_key': 1}, {'other': 'value'}):
        assert client.patch('/api/settings/danzhengtong', json=payload).status_code == 400
    assert client.patch('/api/settings/danzhengtong', json=credentials(),
                        headers={'Origin': 'https://other.example'}).status_code == 403
    assert client.post('/api/settings/danzhengtong/test').status_code == 400


def test_test_flow_no_secrets_or_records(client, monkeypatch):
    client.patch('/api/settings/danzhengtong', json=credentials())
    seen = []
    def recognize(self, path, file_name=None):
        assert path.read_bytes() == b'image'
        seen.append(path)
        return {}, {}
    monkeypatch.setattr(dzt.Client, 'recognize', recognize)
    response = client.post('/api/settings/danzhengtong/test',
                           data={'file': (io.BytesIO(b'image'), 'test.png')})
    assert response.json['status'] == '测试通过'
    assert not seen[0].exists()
    assert client.get('/api/settings/danzhengtong').json['status'] == '测试通过'
    client.patch('/api/settings/danzhengtong', json={'app_id': 'new-id'})
    assert client.get('/api/settings/danzhengtong').json['status'] == '已配置'
    def fail(self, path, file_name=None):
        raise RuntimeError('failed with secret-key secret-app secret-id')
    monkeypatch.setattr(dzt.Client, 'recognize', fail)
    response = client.post('/api/settings/danzhengtong/test',
                           data={'file': (io.BytesIO(b'image'), 'test.png')})
    assert response.status_code == 502
    assert 'secret-' not in response.get_data(as_text=True)


def test_configuration_changed_during_test(client, monkeypatch):
    client.patch('/api/settings/danzhengtong', json=credentials())
    def recognize(self, path, file_name=None):
        dzt.set_software_settings(dzt.Settings())
        return {}, {}
    monkeypatch.setattr(dzt.Client, 'recognize', recognize)
    assert client.post('/api/settings/danzhengtong/test',
                       data={'file': (io.BytesIO(b'image'), 'test.png')}).status_code == 409
