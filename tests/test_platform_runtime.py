from pathlib import Path

import app as app_module


def test_node_resolution_honors_valid_configured_executable(tmp_path: Path, monkeypatch):
    executable = tmp_path / "node.exe"
    executable.write_bytes(b"")
    monkeypatch.setenv("WORKSPACE_NODE", str(executable))
    monkeypatch.setattr(app_module.shutil, "which", lambda _name: None)
    assert app_module.resolve_node_executable() == executable.resolve()


def test_invalid_configured_node_does_not_fall_back_silently(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_NODE", str(tmp_path / "missing-node.exe"))
    monkeypatch.setattr(app_module.shutil, "which", lambda _name: "/unexpected/node")
    assert app_module.resolve_node_executable() is None


def test_node_resolution_uses_path_on_windows_style_install(monkeypatch):
    monkeypatch.delenv("WORKSPACE_NODE", raising=False)
    monkeypatch.setattr(app_module.shutil, "which", lambda name: "C:/Program Files/nodejs/node.exe")
    assert str(app_module.resolve_node_executable()).replace("\\", "/").endswith(
        "C:/Program Files/nodejs/node.exe"
    )


def test_excel_export_does_not_inherit_paddle_openmp_workaround(monkeypatch):
    monkeypatch.setenv("KMP_USE_SHM", "0")
    monkeypatch.setenv("RECEIPT_TEST_ENV", "kept")

    environment = app_module.export_subprocess_environment()

    assert "KMP_USE_SHM" not in environment
    assert environment["RECEIPT_TEST_ENV"] == "kept"


def test_index_displays_active_interpreter(monkeypatch):
    monkeypatch.setattr(app_module, "default_backend", lambda: "paddle")
    monkeypatch.setattr(app_module, "backend_catalog", lambda: [{
        "id": "paddle", "label": "Paddle", "available": True,
        "reason": "", "model_home": "D:/OCR", "model_name": "Mobile",
    }])
    response = app_module.app.test_client().get("/")
    assert response.status_code == 200
    assert app_module.sys.executable.encode() in response.data
