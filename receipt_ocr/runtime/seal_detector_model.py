"""Locate the bundled QingTong detector without requiring the delivery disk.

Source checkouts read the versioned asset directly. Installed builds publish a
complete model directory into the same install-local cache as the OCR models.
An existing installed model always wins; incomplete copies are never published.
"""

from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Lock

from .model_paths import ModelCacheError, prepare_model_cache


_BUNDLED_MODEL_DIR = Path(__file__).resolve().parents[1] / "assets" / "models"
_MODEL_FILENAME = "qingtong_seal_det.onnx"
_METADATA_FILENAME = "qingtong_seal_det.metadata.json"
_INSTALLED_MODEL_NAME = "QingTong-YOLO11s-seal_det"
_COPY_LOCK = Lock()


def _valid_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _require_file(path: Path) -> Path:
    if not _valid_file(path):
        raise FileNotFoundError(f"本地印章检测模型不存在或文件为空：{path}")
    return path


def seal_detector_model_path(
    *,
    frozen: bool | None = None,
    environ: MutableMapping[str, str] | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the local detector, copying bundled resources only when frozen."""
    is_frozen = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
    bundled = _BUNDLED_MODEL_DIR / _MODEL_FILENAME
    if not is_frozen:
        return _require_file(bundled)

    root = prepare_model_cache(frozen=True, environ=environ, executable=executable)
    directory = root / "official_models" / _INSTALLED_MODEL_NAME
    installed = directory / "inference.onnx"
    with _COPY_LOCK:
        if directory.exists() or directory.is_symlink():
            # Keep user/previous-install files, including when bundled resources
            # are absent. An incomplete existing directory needs explicit repair.
            return _require_file(installed)
        _require_file(bundled)
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}.installing-", dir=directory.parent))
        try:
            shutil.copyfile(bundled, staging / "inference.onnx")
            metadata = _BUNDLED_MODEL_DIR / _METADATA_FILENAME
            if metadata.is_file():
                shutil.copyfile(metadata, staging / "metadata.json")
            try:
                staging.rename(directory)
            except OSError:
                # A second process may have installed the complete directory
                # while this process copied. Never overwrite its model.
                if not _valid_file(installed):
                    raise
        except OSError as exc:
            raise ModelCacheError(f"本地印章检测模型安装失败：{installed}。请检查安装位置和可用空间。") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return _require_file(installed)
