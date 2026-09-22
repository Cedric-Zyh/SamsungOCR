import app as app_module


def test_index_displays_active_interpreter(monkeypatch):
    monkeypatch.setattr(app_module, "default_backend", lambda: "paddle")
    monkeypatch.setattr(app_module, "backend_catalog", lambda: [{
        "id": "paddle", "label": "Paddle", "available": True,
        "reason": "", "model_home": "D:/OCR", "model_name": "Mobile",
    }])
    response = app_module.app.test_client().get("/")
    assert response.status_code == 200
    assert app_module.sys.executable.encode() in response.data
