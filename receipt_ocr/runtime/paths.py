"""Filesystem locations used by the application.

The old Flask entry point calculated these paths inline while also creating
services.  This module makes the deployment-specific path decision explicit;
callers can still unpack the same individual paths during the migration.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    resource_dir: Path
    data_dir: Path
    storage_dir: Path
    upload_dir: Path
    preview_dir: Path
    artifact_dir: Path
    export_dir: Path
    database_path: Path
    ground_truth_path: Path


def build_runtime_paths(base_dir: Path | None = None,
                        *, frozen: bool | None = None,
                        environ: dict[str, str] | None = None) -> RuntimePaths:
    """Build paths for a source checkout or a packaged application.

    ``environ`` is injectable so path selection can be tested without
    modifying process-wide environment variables.
    """

    base = Path(base_dir or Path(__file__).resolve().parents[2])
    env = os.environ if environ is None else environ
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    resource_dir = Path(getattr(sys, "_MEIPASS", base))
    if is_frozen:
        default_storage = Path(
            env.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        ) / "SamsungReceipt"
        storage_dir = Path(
            env.get("SAMSUNG_RECEIPT_DATA_DIR", str(default_storage))
        ).expanduser().resolve()
    else:
        storage_dir = base / "storage"
    data_dir = resource_dir / "数据"
    return RuntimePaths(
        resource_dir=resource_dir,
        data_dir=data_dir,
        storage_dir=storage_dir,
        upload_dir=storage_dir / "uploads",
        preview_dir=storage_dir / "previews",
        artifact_dir=storage_dir / "artifacts",
        export_dir=storage_dir / "exports",
        database_path=storage_dir / "results.db",
        ground_truth_path=data_dir / "ground_truth.json",
    )

