from pathlib import Path

import pytest

from receipt_ocr.runtime import seal_detector_model as model


@pytest.fixture
def bundled(monkeypatch, tmp_path):
    directory = tmp_path / "bundle" / "receipt_ocr" / "assets" / "models"
    directory.mkdir(parents=True)
    (directory / "qingtong_seal_det.onnx").write_bytes(b"bundled-model")
    (directory / "qingtong_seal_det.metadata.json").write_text('{"task":"detect"}', encoding="utf-8")
    monkeypatch.setattr(model, "_BUNDLED_MODEL_DIR", directory)
    return directory


def _environment(tmp_path):
    return {
        "SAMSUNG_RECEIPT_INSTALL_DIR": str(tmp_path / "中文 空格安装目录"),
        "HOME": str(tmp_path / "user"),
        "LOCALAPPDATA": str(tmp_path / "user" / "AppData" / "Local"),
        "PADDLE_MODEL_HOME": str(tmp_path / "old-cache"),
        "PADDLE_PDX_CACHE_HOME": str(tmp_path / "another-old-cache"),
    }


def test_source_reads_bundled_model_without_preparing_any_cache(monkeypatch, bundled, tmp_path):
    def unexpected(**kwargs):
        pytest.fail("source detector should not prepare or write an external cache")
    monkeypatch.setattr(model, "prepare_model_cache", unexpected)
    assert model.seal_detector_model_path(frozen=False, environ=_environment(tmp_path)) == bundled / "qingtong_seal_det.onnx"


def test_installed_model_uses_install_local_cache_and_preserves_provenance(bundled, tmp_path):
    env = _environment(tmp_path)
    target = model.seal_detector_model_path(frozen=True, environ=env)
    expected = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models" / "official_models" / "QingTong-YOLO11s-seal_det" / "inference.onnx"
    assert target == expected
    assert target.read_bytes() == b"bundled-model"
    assert (target.parent / "metadata.json").read_text(encoding="utf-8") == '{"task":"detect"}'
    assert Path(env["PADDLE_MODEL_HOME"]) == expected.parents[2]
    assert Path(env["PADDLE_PDX_CACHE_HOME"]) == expected.parents[2]
    assert not list(target.parent.parent.glob(".*.installing-*"))


def test_existing_installed_model_is_never_overwritten_even_without_bundle(bundled, tmp_path):
    env = _environment(tmp_path)
    target = model.seal_detector_model_path(frozen=True, environ=env)
    target.write_bytes(b"existing-model")
    (bundled / "qingtong_seal_det.onnx").unlink()
    assert model.seal_detector_model_path(frozen=True, environ=env) == target
    assert target.read_bytes() == b"existing-model"


def test_failed_copy_does_not_publish_partial_model_and_can_retry(monkeypatch, bundled, tmp_path):
    env = _environment(tmp_path)
    real_copy = model.shutil.copyfile
    def fail_copy(source, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(model.shutil, "copyfile", fail_copy)
    with pytest.raises(model.ModelCacheError, match="本地印章检测模型安装失败"):
        model.seal_detector_model_path(frozen=True, environ=env)
    official = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models" / "official_models"
    assert not (official / "QingTong-YOLO11s-seal_det").exists()
    assert not list(official.glob(".*.installing-*"))
    assert (bundled / "qingtong_seal_det.onnx").read_bytes() == b"bundled-model"
    monkeypatch.setattr(model.shutil, "copyfile", real_copy)
    assert model.seal_detector_model_path(frozen=True, environ=env).read_bytes() == b"bundled-model"


def test_incomplete_existing_directory_is_preserved_and_reported(bundled, tmp_path):
    env = _environment(tmp_path)
    directory = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models" / "official_models" / "QingTong-YOLO11s-seal_det"
    directory.mkdir(parents=True)
    existing = directory / "custom-file"
    existing.write_bytes(b"preserve")
    with pytest.raises(FileNotFoundError, match="本地印章检测模型不存在"):
        model.seal_detector_model_path(frozen=True, environ=env)
    assert existing.read_bytes() == b"preserve"
    assert not (directory / "inference.onnx").exists()
