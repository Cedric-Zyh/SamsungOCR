from pathlib import Path

import pytest

from receipt_ocr.seal_api import SealApiClient, resolve_seal_recognition_mode


def test_seal_recognition_mode_defaults_to_local():
    assert resolve_seal_recognition_mode(None) == "local"
    assert resolve_seal_recognition_mode("qingtong") == "qingtong"
    with pytest.raises(ValueError):
        resolve_seal_recognition_mode("unknown")


def test_client_reads_api_key_from_file(tmp_path: Path, monkeypatch):
    key_file = tmp_path / "seal_api_key"
    key_file.write_text("file-key\n", encoding="utf-8")
    monkeypatch.delenv("SEAL_API_KEY", raising=False)
    monkeypatch.setenv("SEAL_API_KEY_FILE", str(key_file))

    client = SealApiClient()

    assert client.api_key == "file-key"
    assert client.enabled is True


def test_environment_api_key_overrides_file(tmp_path: Path, monkeypatch):
    key_file = tmp_path / "seal_api_key"
    key_file.write_text("file-key", encoding="utf-8")
    monkeypatch.setenv("SEAL_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("SEAL_API_KEY", "environment-key")

    assert SealApiClient().api_key == "environment-key"


def test_qingtong_request_uses_configured_endpoint_and_header(
    tmp_path: Path, monkeypatch
):
    image = tmp_path / "seal.jpg"
    image.write_bytes(b"image")
    monkeypatch.setenv("SEAL_API_URL", "https://seal.qingtong.cn")
    monkeypatch.setenv("SEAL_API_KEY", "test-key")
    captured = {}

    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"code": 200, "message": "success", "data": {"text": "测试印章"}}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("receipt_ocr.seal_api.requests.post", fake_post)
    result = SealApiClient().recognize(image)

    assert captured["url"] == "https://seal.qingtong.cn/api/v1/recognize"
    assert captured["headers"] == {"X-API-Key": "test-key"}
    assert "files" in captured["files"]
    assert result["ok"] is True
    assert result["business_code"] == 200
    assert result["response"]["data"]["text"] == "测试印章"


def test_http_200_business_failure_falls_back_to_local(tmp_path: Path, monkeypatch):
    image = tmp_path / "seal.jpg"
    image.write_bytes(b"image")
    monkeypatch.setenv("SEAL_API_KEY", "test-key")

    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"code": 503, "message": "busy", "data": None}

    monkeypatch.setattr(
        "receipt_ocr.seal_api.requests.post", lambda *_args, **_kwargs: Response()
    )
    result = SealApiClient().recognize(image)

    assert result["ok"] is False
    assert result["business_code"] == 503
    assert "已保留本地识别" in result["message"]


def test_network_failure_falls_back_to_local(tmp_path: Path, monkeypatch):
    image = tmp_path / "seal.jpg"
    image.write_bytes(b"image")
    monkeypatch.setenv("SEAL_API_KEY", "test-key")

    def fail(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr("receipt_ocr.seal_api.requests.post", fail)
    result = SealApiClient().recognize(image)

    assert result["ok"] is False
    assert "已保留本地识别" in result["message"]
