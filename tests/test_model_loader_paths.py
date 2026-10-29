"""Every packaged OCR loader must use the installation's shared model cache."""

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def packaged_loaders(tmp_path, monkeypatch):
    from receipt_ocr.providers import orientation, paddle_runtime
    from receipt_ocr.runtime import model_paths

    install_dir = tmp_path / "中文 空格安装目录"
    install_dir.mkdir()
    user_dir = tmp_path / "模拟用户"
    user_dir.mkdir()
    local_data = user_dir / "AppData" / "Local"
    local_data.mkdir(parents=True)
    old_pdx = tmp_path / "旧PaddleX缓存"
    old_paddle = tmp_path / "旧Paddle缓存"
    old_pdx.mkdir()
    old_paddle.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys, "executable",
        str(install_dir / "resources" / "SamsungReceipt" / "SamsungReceipt.exe"),
    )
    monkeypatch.setenv("HOME", str(user_dir))
    monkeypatch.setenv("USERPROFILE", str(user_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(local_data))
    monkeypatch.setenv("SAMSUNG_RECEIPT_INSTALL_DIR", str(install_dir))
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(old_pdx))
    monkeypatch.setenv("PADDLE_MODEL_HOME", str(old_paddle))
    # Native cache migration must never inspect the machine's real home.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_dir))

    monkeypatch.setattr(paddle_runtime, "_PIPELINES", {})
    monkeypatch.setattr(paddle_runtime, "_LINE_RECOGNIZERS", {})
    monkeypatch.setattr(paddle_runtime, "_SEAL_DETECTORS", {})
    monkeypatch.setattr(orientation, "_classifier", None)
    monkeypatch.setattr(orientation, "_doc_classifier", None)
    monkeypatch.setattr(model_paths, "_PREPARED_ROOTS", set())
    monkeypatch.setattr(model_paths, "_ROOT_LOCKS", {})
    # Restore environment values changed by cache setup when this test ends.
    for key in ("PADDLE_PDX_MODEL_SOURCE", "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"):
        monkeypatch.setenv(key, "test")

    calls = []
    model_dir = (install_dir / "models").resolve()
    fake_paddleocr = ModuleType("paddleocr")

    def constructor(name):
        def construct(**kwargs):
            cache_dirs = {
                key: Path(os.environ[key]).resolve()
                for key in ("PADDLE_PDX_CACHE_HOME", "PADDLE_MODEL_HOME")
            }
            assert cache_dirs == {
                "PADDLE_PDX_CACHE_HOME": model_dir,
                "PADDLE_MODEL_HOME": model_dir,
            }, f"{name} constructed before the installation cache was configured"
            calls.append(SimpleNamespace(name=name, kwargs=kwargs, cache_dirs=cache_dirs))
            return object()

        return construct

    for name in (
        "PaddleOCR", "TextRecognition", "DocImgOrientationClassification",
        "TextLineOrientationClassification",
    ):
        setattr(fake_paddleocr, name, constructor(name))
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)

    return SimpleNamespace(
        paddle=paddle_runtime, orientation=orientation, model_paths=model_paths,
        calls=calls, model_dir=model_dir,
    )


@pytest.mark.parametrize("engine", [None, "onnxruntime"], ids=["native", "onnx"])
@pytest.mark.parametrize(
    "loader,constructor_name,uses_configured_engine,model_name",
    [
        ("page", "PaddleOCR", True, "PP-OCRv6_small_det"),
        ("line", "TextRecognition", True, "PP-OCRv6_small_rec"),
        ("seal", "PaddleOCR", False, "PP-OCRv4_mobile_seal_det"),
        ("document_direction", "DocImgOrientationClassification", False,
         "PP-LCNet_x1_0_doc_ori"),
        ("line_direction", "TextLineOrientationClassification", True,
         "PP-LCNet_x0_25_textline_ori"),
    ],
)
def test_all_loaders_configure_installation_cache_before_construction(
    packaged_loaders, monkeypatch, engine, loader, constructor_name,
    uses_configured_engine, model_name,
):
    state = packaged_loaders
    # No ONNX Runtime import, model download or inference is needed to verify
    # that both native and ONNX construction receive the same cache root.
    monkeypatch.setattr(
        state.paddle, "_engine_kwargs", lambda: {"engine": engine} if engine else {},
    )
    monkeypatch.setattr(state.paddle, "paddle_engine", lambda: engine)
    load = {
        "page": lambda: state.paddle._pipeline("v6"),
        "line": lambda: state.paddle._line_recognizer("v6"),
        "seal": state.paddle._seal_pipeline,
        "document_direction": state.orientation._document_classifier,
        "line_direction": state.orientation._line_classifier,
    }[loader]

    model = load()

    assert len(state.calls) == 1
    call = state.calls[0]
    assert call.name == constructor_name
    assert call.kwargs.get("model_name", call.kwargs.get("text_detection_model_name")) == model_name
    assert call.kwargs.get("engine") == (engine if uses_configured_engine else None)
    assert call.kwargs["device"] == "cpu"
    assert state.model_dir.is_dir()
    assert state.paddle.paddle_model_home() == state.model_dir
    assert load() is model
    assert len(state.calls) == 1


def test_frozen_entrypoint_sets_model_cache_before_web_application_is_read(tmp_path):
    app_file = Path(__file__).resolve().parents[1] / "app.py"
    install_dir = tmp_path / "中文 空格安装目录"
    install_dir.mkdir()
    user_dir = tmp_path / "模拟用户"
    user_dir.mkdir()
    script = r'''
import os
from pathlib import Path
import runpy
import sys
from types import ModuleType

app_file, install_dir, user_dir = map(Path, sys.argv[1:])
os.environ.update({
    "HOME": str(user_dir),
    "USERPROFILE": str(user_dir),
    "LOCALAPPDATA": str(user_dir / "AppData" / "Local"),
    "SAMSUNG_RECEIPT_INSTALL_DIR": str(install_dir),
    "PADDLE_PDX_CACHE_HOME": str(user_dir / "old-pdx"),
    "PADDLE_MODEL_HOME": str(user_dir / "old-paddle"),
})
# Import ordinary dependencies before simulating a Windows frozen entrypoint;
# no actual PaddleOCR/PaddleX model package is imported by this module.
import receipt_ocr.runtime.model_paths
sys.frozen = True
sys.platform = "win32"
sys.executable = str(install_dir / "resources" / "SamsungReceipt" / "SamsungReceipt.exe")
expected = (install_dir / "models").resolve()

class GuardedWebApplication(ModuleType):
    def __getattribute__(self, name):
        if name in {"app", "run"}:
            for key in ("PADDLE_PDX_CACHE_HOME", "PADDLE_MODEL_HOME"):
                assert Path(os.environ[key]).resolve() == expected, (key, os.environ[key])
            assert "paddleocr" not in sys.modules
            assert "paddlex" not in sys.modules
        return super().__getattribute__(name)

web = GuardedWebApplication("receipt_ocr.web.application")
web.__all__ = ["app", "run"]
web.app = object()
web.run = lambda: None
sys.modules["receipt_ocr.web.application"] = web
runpy.run_path(str(app_file), run_name="entrypoint_import_test")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(app_file), str(install_dir), str(user_dir)],
        cwd=app_file.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
