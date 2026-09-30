"""Retention and file cleanup services for the local web application."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable


class StorageMaintenance:
    """Coordinate database retention with safe removal of unreferenced files."""

    def __init__(self, database, *, upload_dir: Path, preview_dir: Path,
                 artifact_dir: Path, export_dir: Path,
                 logger: object | None = None):
        self.database = database
        self.upload_dir = Path(upload_dir)
        self.preview_dir = Path(preview_dir)
        self.artifact_dir = Path(artifact_dir)
        self.export_dir = Path(export_dir)
        self.logger = logger

    def storage_references(self) -> tuple[set[str], set[str], set[str]]:
        uploads, previews, artifacts = set(), set(), set()
        with self.database.connect() as connection:
            for row in connection.execute(
                "SELECT stored_name,preview_name,result_json FROM results WHERE deleted_at=''"
            ):
                stored_name = str(row["stored_name"] or "")
                if stored_name and not stored_name.startswith("sample:"):
                    uploads.add(Path(stored_name).name)
                preview_name = str(row["preview_name"] or "")
                if preview_name:
                    previews.add(Path(preview_name).name)
                artifacts.update(re.findall(
                    r"/files/artifacts/([A-Za-z0-9_-]+)(?:/|\b)", row["result_json"] or ""
                ))
            has_jobs = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_jobs'"
            ).fetchone()
            if has_jobs:
                for row in connection.execute(
                    "SELECT stored_name FROM recognition_jobs WHERE stored_name<>''"
                ):
                    stored_name = str(row["stored_name"] or "")
                    if not stored_name.startswith("sample:"):
                        uploads.add(Path(stored_name).name)
        return uploads, previews, artifacts

    def cleanup_expired_storage(self, purge: dict) -> dict[str, int]:
        try:
            cutoff_timestamp = datetime.fromisoformat(
                str(purge.get("cutoff"))
            ).timestamp()
        except (TypeError, ValueError, OverflowError):
            cutoff_timestamp = None
        referenced_uploads, referenced_previews, referenced_artifacts = self.storage_references()
        removed = {"uploads": 0, "previews": 0, "artifacts": 0, "exports": 0}
        artifact_tokens = set()
        for row in purge.get("records") or []:
            stored_name = str(row.get("stored_name") or "")
            if stored_name and not stored_name.startswith("sample:"):
                candidate = (self.upload_dir / Path(stored_name).name).resolve()
                if (candidate.parent == self.upload_dir.resolve()
                        and candidate.name not in referenced_uploads
                        and candidate.is_file()):
                    candidate.unlink(missing_ok=True)
                    removed["uploads"] += 1
            preview_name = str(row.get("preview_name") or "")
            if preview_name:
                candidate = (self.preview_dir / Path(preview_name).name).resolve()
                if (candidate.parent == self.preview_dir.resolve()
                        and candidate.name not in referenced_previews
                        and candidate.is_file()):
                    candidate.unlink(missing_ok=True)
                    removed["previews"] += 1
            artifact_tokens.update(re.findall(
                r"/files/artifacts/([A-Za-z0-9_-]+)(?:/|\b)",
                row.get("result_json") or "",
            ))
        for token in artifact_tokens - referenced_artifacts:
            candidate = (self.artifact_dir / token).resolve()
            if candidate.parent == self.artifact_dir.resolve() and candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
                removed["artifacts"] += 1
        if cutoff_timestamp is None:
            return removed

        def is_old(path: Path) -> bool:
            try:
                return path.stat().st_mtime < cutoff_timestamp
            except OSError:
                return False

        for directory, references, key in (
            (self.upload_dir, referenced_uploads, "uploads"),
            (self.preview_dir, referenced_previews, "previews"),
        ):
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file() and path.name not in references and is_old(path):
                        path.unlink(missing_ok=True)
                        removed[key] += 1
        if self.artifact_dir.is_dir():
            for path in self.artifact_dir.iterdir():
                if path.is_dir() and path.name not in referenced_artifacts and is_old(path):
                    shutil.rmtree(path, ignore_errors=True)
                    removed["artifacts"] += 1
        if self.export_dir.is_dir():
            for path in self.export_dir.iterdir():
                if path.is_file() and is_old(path):
                    path.unlink(missing_ok=True)
                    removed["exports"] += 1
        return removed

    def purge_expired(self) -> dict:
        try:
            purge = self.database.purge_expired()
            purge["files"] = self.cleanup_expired_storage(purge)
            if (purge.get("deleted_results")
                    or any(purge["files"].values())) and self.logger is not None:
                self.logger.info(
                    "已按保留天数清理数据：记录 %s，任务 %s，文件 %s",
                    purge.get("deleted_results", 0), purge.get("deleted_tasks", 0),
                    purge["files"],
                )
            return purge
        except Exception:
            if self.logger is not None:
                self.logger.exception("按保留天数清理数据失败")
            return {
                "retention_days": self.database.get_retention_days(),
                "deleted_results": 0,
                "deleted_jobs": 0,
                "deleted_tasks": 0,
                "files": {},
            }


__all__ = ["StorageMaintenance"]
