"""Model storage checks use temporary caches, never installed OCR runtimes."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Lock
import time

import pytest

from receipt_ocr.runtime import model_paths


@pytest.fixture(autouse=True)
def isolated_preparation_state(monkeypatch, tmp_path):
    monkeypatch.setattr(model_paths, "_ROOT_LOCKS", {})
    monkeypatch.setattr(model_paths, "_PREPARED_ROOTS", set())
    monkeypatch.setattr(model_paths, "_EXTERNAL_MODEL_HOME", tmp_path / "absent-external")


def _environment(tmp_path):
    return {
        "SAMSUNG_RECEIPT_INSTALL_DIR": str(tmp_path / "chosen-install"),
        "LOCALAPPDATA": str(tmp_path / "old-app-data"),
        "HOME": str(tmp_path / "home"),
    }


def _model(parent, name, *, onnx=False, old_native=False):
    directory = parent / "official_models" / name
    directory.mkdir(parents=True)
    (directory / "inference.yml").write_text("Global: {}\n")
    if onnx:
        (directory / "inference.onnx").write_bytes(b"complete-onnx-model")
    else:
        (directory / "inference.pdiparams").write_bytes(b"complete-native-parameters")
        (directory / ("inference.pdmodel" if old_native else "inference.json")).write_text("{}")
    return directory


def test_source_environment_priority_and_query_have_no_mutations(tmp_path):
    env = {"PADDLE_PDX_CACHE_HOME": str(tmp_path / "pdx"),
           "PADDLE_MODEL_HOME": str(tmp_path / "paddle"),
           "SAMSUNG_RECEIPT_INSTALL_DIR": str(tmp_path / "install")}
    before = dict(env)
    assert model_paths.model_home(frozen=False, environ=env) == tmp_path / "pdx"
    assert env == before
    assert not (tmp_path / "pdx").exists()
    env.pop("PADDLE_PDX_CACHE_HOME")
    assert model_paths.model_home(frozen=False, environ=env) == tmp_path / "paddle"


def test_source_preserves_external_disk_then_user_home(monkeypatch, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setattr(model_paths, "_EXTERNAL_MODEL_HOME", external)
    assert model_paths.model_home(frozen=False, environ={}) == external
    external.rmdir()
    assert model_paths.model_home(frozen=False, environ={"HOME": str(tmp_path)}) == tmp_path / ".paddlex"


def test_frozen_explicit_install_ignores_both_inherited_cache_variables(tmp_path):
    env = _environment(tmp_path)
    env.update(PADDLE_PDX_CACHE_HOME=str(tmp_path / "unrelated-pdx"),
               PADDLE_MODEL_HOME=str(tmp_path / "old-models"))
    root = model_paths.model_home(frozen=True, environ=env, executable=tmp_path / "elsewhere/backend.exe")
    assert root == tmp_path / "chosen-install/models"
    assert not root.exists()


@pytest.mark.parametrize("relative", [
    "resources/SamsungReceipt/SamsungReceipt.exe",
    "_up_/resources/SamsungReceipt/SamsungReceipt.exe",
    "resources/samsungreceipt/SamsungReceipt.exe",
])
def test_frozen_standard_service_layout_restores_install_root(tmp_path, relative):
    assert model_paths.model_home(frozen=True, environ={}, executable=tmp_path / relative) == tmp_path / "models"


def test_standalone_install_named_samsungreceipt_is_not_stripped(tmp_path):
    installation = tmp_path / "SamsungReceipt"
    assert model_paths.model_home(
        frozen=True, environ={}, executable=installation / "SamsungReceipt.exe"
    ) == installation / "models"


def test_ambiguous_nested_service_needs_a_confirmed_desktop_shell(tmp_path):
    executable = tmp_path / "chosen/SamsungReceipt/SamsungReceipt.exe"
    assert model_paths.model_home(frozen=True, environ={}, executable=executable) == executable.parent / "models"
    shell = tmp_path / "chosen/samsung-receipt-desktop.exe"
    shell.parent.mkdir()
    shell.write_bytes(b"desktop-shell")
    assert model_paths.model_home(frozen=True, environ={}, executable=executable) == shell.parent / "models"


def test_frozen_default_uses_executable_not_meipass_or_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(model_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(model_paths.sys, "_MEIPASS", str(tmp_path / "unrelated-resources"), raising=False)
    monkeypatch.setattr(model_paths.sys, "executable", str(tmp_path / "installed/SamsungReceipt.exe"))
    monkeypatch.chdir(tmp_path)
    assert model_paths.model_home(environ={}) == tmp_path / "installed/models"


def test_configure_sets_both_variables_without_creating_or_copying(tmp_path):
    env = _environment(tmp_path)
    root = model_paths.configure_model_environment(frozen=True, environ=env)
    assert env["PADDLE_MODEL_HOME"] == env["PADDLE_PDX_CACHE_HOME"] == str(root)
    assert not root.exists()


def test_source_preparation_only_creates_root(monkeypatch, tmp_path):
    env = _environment(tmp_path)
    env["PADDLE_MODEL_HOME"] = str(tmp_path / "source-cache")
    monkeypatch.setattr(model_paths, "_probe_writable", lambda *_: pytest.fail("source run must not probe"))
    monkeypatch.setattr(model_paths, "_migrate_legacy_cache", lambda *_: pytest.fail("source run must not migrate"))
    assert model_paths.prepare_model_cache(frozen=False, environ=env) == tmp_path / "source-cache"
    assert (tmp_path / "source-cache").is_dir()
    assert not model_paths._PREPARED_ROOTS


def test_all_complete_app_private_models_and_fonts_are_copied_without_overwrite(tmp_path):
    env = _environment(tmp_path)
    legacy = Path(env["LOCALAPPDATA"]) / "SamsungReceipt/models"
    names = ["PP-OCRv6_small_det", "PP-OCRv6_small_rec", "PP-OCRv4_mobile_seal_det",
             "PP-LCNet_x1_0_doc_ori", "PP-LCNet_x0_25_textline_ori_onnx", "PP-OCRv6_small_rec_onnx"]
    for name in names:
        _model(legacy, name, onnx=name.endswith("_onnx"))
    _model(legacy, "legacy-native-det", old_native=True)
    fonts = legacy / "fonts"
    fonts.mkdir()
    (fonts / "existing.ttf").write_bytes(b"old-font")
    (fonts / "simfang.ttf").write_bytes(b"complete-font")
    (fonts / "half.ttf.tmp").write_bytes(b"partial-font")
    for folder in ["locks", "temp", "func_ret", "predict_input"]:
        (legacy / folder).mkdir()
        (legacy / folder / "ignore.bin").write_bytes(b"not-stable-cache")
    (legacy / "official_models/archive.zip").write_bytes(b"not-an-installed-model")
    incomplete = legacy / "official_models/half"
    incomplete.mkdir()
    (incomplete / "inference.onnx").write_bytes(b"no-config")
    partial = _model(legacy, "still-downloading.tmp", onnx=True)
    (partial / "inference.onnx.part").write_bytes(b"half")
    zero = _model(legacy, "zero-size", onnx=True)
    (zero / "inference.onnx").write_bytes(b"")
    unrelated = Path(env["HOME"]) / ".paddlex"
    _model(unrelated, "unrelated-global-model")
    target = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models"
    existing = _model(target, names[0])
    (existing / "inference.pdiparams").write_bytes(b"keep-existing-target")
    (target / "fonts").mkdir()
    (target / "fonts/existing.ttf").write_bytes(b"keep-new-font")
    root = model_paths.prepare_model_cache(frozen=True, environ=env)
    assert root == target
    for name in [*names, "legacy-native-det"]:
        assert (root / "official_models" / name / "inference.yml").is_file()
    assert (existing / "inference.pdiparams").read_bytes() == b"keep-existing-target"
    assert (legacy / "official_models" / names[0] / "inference.pdiparams").read_bytes() == b"complete-native-parameters"
    assert (root / "fonts/existing.ttf").read_bytes() == b"keep-new-font"
    assert (root / "fonts/simfang.ttf").read_bytes() == b"complete-font"
    assert sorted(path.name for path in (root / "official_models").iterdir()) == sorted([*names, "legacy-native-det"])
    assert not any((root / name).exists() for name in ["locks", "temp", "func_ret", "predict_input"])
    assert not (root / "fonts/half.ttf.tmp").exists()
    assert not list(root.rglob("*.migrating-*"))
    assert not list(root.glob(".samsung-model-probe-*"))
    assert (unrelated / "official_models/unrelated-global-model/inference.yml").is_file()


def test_model_links_are_not_followed_outside_private_cache(tmp_path):
    env = _environment(tmp_path)
    legacy = Path(env["LOCALAPPDATA"]) / "SamsungReceipt/models"
    source = _model(legacy, "model-with-link")
    outside = tmp_path / "unrelated-private-file"
    outside.write_bytes(b"must-not-be-copied")
    try:
        (source / "unrelated").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    target = model_paths.prepare_model_cache(frozen=True, environ=env)
    assert not (target / "official_models/model-with-link").exists()


def test_copy_failure_keeps_source_cleans_staging_and_can_retry(monkeypatch, tmp_path):
    env = _environment(tmp_path)
    legacy = Path(env["LOCALAPPDATA"]) / "SamsungReceipt/models"
    source = _model(legacy, "PP-OCRv6_small_rec_onnx", onnx=True)
    real_copy = model_paths.shutil.copytree
    def fail_copy(src, dst, **kwargs):
        (Path(dst) / "partial-file").write_bytes(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(model_paths.shutil, "copytree", fail_copy)
    with pytest.raises(model_paths.ModelCacheError, match="模型目录不可写或缓存迁移失败"):
        model_paths.prepare_model_cache(frozen=True, environ=env)
    target = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models"
    assert not (target / "official_models" / source.name).exists()
    assert not list(target.rglob("*.migrating-*"))
    assert (source / "inference.onnx").read_bytes() == b"complete-onnx-model"
    assert target not in model_paths._PREPARED_ROOTS
    monkeypatch.setattr(model_paths.shutil, "copytree", real_copy)
    model_paths.prepare_model_cache(frozen=True, environ=env)
    assert (target / "official_models" / source.name / "inference.onnx").read_bytes() == b"complete-onnx-model"


def test_unwritable_selected_install_reports_error_without_appdata_fallback(monkeypatch, tmp_path):
    env = _environment(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("read-only install")
    monkeypatch.setattr(model_paths.tempfile, "NamedTemporaryFile", denied)
    with pytest.raises(model_paths.ModelCacheError) as error:
        model_paths.prepare_model_cache(frozen=True, environ=env)
    target = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models"
    assert str(target) in str(error.value)
    assert env["PADDLE_PDX_CACHE_HOME"] == env["PADDLE_MODEL_HOME"] == str(target)
    assert not (Path(env["LOCALAPPDATA"]) / "SamsungReceipt/models").exists()
    assert target not in model_paths._PREPARED_ROOTS


def test_concurrent_preparation_migrates_once_per_successful_root(monkeypatch, tmp_path):
    env = _environment(tmp_path)
    legacy = Path(env["LOCALAPPDATA"]) / "SamsungReceipt/models"
    source = _model(legacy, "PP-OCRv6_small_det_onnx", onnx=True)
    calls = []
    counter_lock = Lock()
    real_migration = model_paths._migrate_legacy_cache
    def observed(root, environment):
        with counter_lock:
            calls.append(root)
        time.sleep(.025)
        real_migration(root, environment)
    monkeypatch.setattr(model_paths, "_migrate_legacy_cache", observed)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: model_paths.prepare_model_cache(frozen=True, environ=dict(env)), range(16)))
    assert len(set(results)) == 1
    assert calls == [results[0]]
    assert (results[0] / "official_models" / source.name / "inference.onnx").read_bytes() == b"complete-onnx-model"


def test_existing_target_created_during_copy_is_not_replaced(monkeypatch, tmp_path):
    env = _environment(tmp_path)
    legacy = Path(env["LOCALAPPDATA"]) / "SamsungReceipt/models"
    source = _model(legacy, "PP-OCRv6_small_rec_onnx", onnx=True)
    target = Path(env["SAMSUNG_RECEIPT_INSTALL_DIR"]) / "models/official_models" / source.name
    real_copy = model_paths.shutil.copytree
    def concurrent_install(src, dst, **kwargs):
        result = real_copy(src, dst, **kwargs)
        target.mkdir()
        (target / "keep.txt").write_text("existing target wins")
        return result
    monkeypatch.setattr(model_paths.shutil, "copytree", concurrent_install)
    model_paths.prepare_model_cache(frozen=True, environ=env)
    assert (target / "keep.txt").read_text() == "existing target wins"
    assert not (target / "inference.onnx").exists()
    assert not list(target.parent.glob("*.migrating-*"))
