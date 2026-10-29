"""Resolve model storage before importing PaddleX; prepare it only when loading.

Installed builds own one model cache next to the desktop executable. Source
checkouts retain their existing environment-variable and external-disk choices.
This module deliberately imports no OCR packages and never moves old caches.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
import os
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Lock


_EXTERNAL_MODEL_HOME = Path("/Volumes/SN770/OCR")
_SHELL_EXECUTABLES = ("samsung-receipt-desktop.exe", "三星回单核验台.exe")
_PARTIAL_SUFFIXES = (".tmp", ".temp", ".part", ".partial", ".incomplete", ".download")
_FONT_SUFFIXES = {".ttf", ".ttc", ".otf", ".woff", ".woff2"}
_ROOTS_LOCK = Lock()
_ROOT_LOCKS: dict[Path, Lock] = {}
_PREPARED_ROOTS: set[Path] = set()


class ModelCacheError(RuntimeError):
    """The installed cache could not be prepared at its required location."""


def _is_frozen(frozen: bool | None) -> bool:
    return bool(getattr(sys, "frozen", False) if frozen is None else frozen)


def _home(env: Mapping[str, str]) -> Path:
    configured = env.get("USERPROFILE") or env.get("HOME")
    return Path(configured).expanduser() if configured else Path.home()


def _absolute(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve()


def _installation_root(executable: str | os.PathLike[str]) -> Path:
    executable_path = _absolute(executable)
    directory = executable_path.parent
    if directory.name.casefold() == "samsungreceipt":
        parent = directory.parent
        if parent.name.casefold() == "resources":
            installation = parent.parent
            if installation.name.casefold() == "_up_":
                installation = installation.parent
            return installation
        # A folder named SamsungReceipt can itself be a standalone install.
        # Strip it only when the actual desktop shell exists beside it.
        if any((parent / name).is_file() for name in _SHELL_EXECUTABLES):
            return parent
    return directory


def model_home(
    *,
    frozen: bool | None = None,
    environ: Mapping[str, str] | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> Path:
    """Select a model root without creating directories or changing the process.

    The small existence checks preserve source external-disk selection and
    distinguish a nested service from a standalone SamsungReceipt directory.
    Neither the current directory nor PyInstaller's resource extraction path
    determines an installed cache location.
    """
    env = os.environ if environ is None else environ
    if _is_frozen(frozen):
        configured = env.get("SAMSUNG_RECEIPT_INSTALL_DIR", "").strip()
        installation = (
            _absolute(configured)
            if configured
            else _installation_root(executable or sys.executable)
        )
        return installation / "models"
    configured = env.get("PADDLE_PDX_CACHE_HOME") or env.get("PADDLE_MODEL_HOME")
    if configured:
        return _absolute(configured)
    if _EXTERNAL_MODEL_HOME.is_dir():
        return _EXTERNAL_MODEL_HOME.resolve()
    return (_home(env) / ".paddlex").resolve()


def configure_model_environment(
    *,
    frozen: bool | None = None,
    environ: MutableMapping[str, str] | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> Path:
    """Unify both cache variables before PaddleX reads them at import time."""
    env = os.environ if environ is None else environ
    root = model_home(frozen=frozen, environ=env, executable=executable)
    env["PADDLE_MODEL_HOME"] = str(root)
    env["PADDLE_PDX_CACHE_HOME"] = str(root)
    return root


def _partial_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered.startswith(".") or lowered.endswith(_PARTIAL_SUFFIXES)


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.stat().st_size > 0


def _complete_model(directory: Path) -> bool:
    if not directory.is_dir() or directory.is_symlink() or _partial_name(directory.name):
        return False
    if not _nonempty_file(directory / "inference.yml"):
        return False
    native = _nonempty_file(directory / "inference.pdiparams") and any(
        _nonempty_file(directory / name) for name in ("inference.json", "inference.pdmodel")
    )
    onnx = _nonempty_file(directory / "inference.onnx")
    if not (native or onnx):
        return False
    # Never follow a link out of the app's private cache, or publish a model
    # directory that still contains visible download/partial-file markers.
    return not any(
        item.is_symlink() or item.name.casefold().endswith(_PARTIAL_SUFFIXES)
        for item in directory.rglob("*")
    )


def _copy_directory_atomic(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.migrating-", dir=destination.parent))
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        if destination.exists() or destination.is_symlink():
            return
        # The per-root lock protects competing in-process preparations. On
        # Windows rename also refuses an existing destination from another app.
        try:
            temporary.rename(destination)
        except FileExistsError:
            if not destination.exists():
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _copy_font_atomic(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.migrating-", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        if destination.exists() or destination.is_symlink():
            return
        try:
            temporary.rename(destination)
        except FileExistsError:
            if not destination.exists():
                raise
    finally:
        temporary.unlink(missing_ok=True)


def _migrate_legacy_cache(root: Path, env: Mapping[str, str]) -> None:
    local_app_data = env.get("LOCALAPPDATA")
    legacy = (
        Path(local_app_data) if local_app_data else _home(env) / "AppData" / "Local"
    ) / "SamsungReceipt" / "models"
    if legacy.resolve() == root or not legacy.is_dir() or legacy.is_symlink():
        return
    models = legacy / "official_models"
    if models.is_dir() and not models.is_symlink():
        for source in sorted(models.iterdir()):
            if _complete_model(source):
                _copy_directory_atomic(source, root / "official_models" / source.name)
    fonts = legacy / "fonts"
    if fonts.is_dir() and not fonts.is_symlink():
        for source in sorted(fonts.rglob("*")):
            relative = source.relative_to(fonts)
            if (
                source.suffix.casefold() in _FONT_SUFFIXES
                and _nonempty_file(source)
                and not any(_partial_name(part) for part in relative.parts)
                and not any(parent.is_symlink() for parent in source.parents if parent != fonts)
            ):
                _copy_font_atomic(source, root / "fonts" / relative)


def _probe_writable(root: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix=".samsung-model-probe-", dir=root) as probe:
        probe.write(b"model-cache-write-check\n")
        probe.flush()


def prepare_model_cache(
    *,
    frozen: bool | None = None,
    environ: MutableMapping[str, str] | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> Path:
    """Prepare storage at first model load, preserving the old cache on failure.

    Frozen builds probe the selected install directory and copy complete
    app-private models once per successful root. Existing targets win. Source
    runs retain their previous mkdir-only behavior without migration/probes.
    """
    env = os.environ if environ is None else environ
    root = configure_model_environment(frozen=frozen, environ=env, executable=executable)
    if not _is_frozen(frozen):
        root.mkdir(parents=True, exist_ok=True)
        return root
    with _ROOTS_LOCK:
        lock = _ROOT_LOCKS.setdefault(root, Lock())
    with lock:
        if root in _PREPARED_ROOTS:
            return root
        try:
            root.mkdir(parents=True, exist_ok=True)
            _probe_writable(root)
            _migrate_legacy_cache(root, env)
        except OSError as exc:
            raise ModelCacheError(
                f"模型目录不可写或缓存迁移失败：{root}。请检查该安装位置的写入权限和可用空间。"
            ) from exc
        _PREPARED_ROOTS.add(root)
    return root
