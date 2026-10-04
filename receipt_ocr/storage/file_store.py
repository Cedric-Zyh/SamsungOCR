"""Safe file operations for uploaded receipts and generated artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ReceiptFileStore:
    """Resolve receipt sources without letting record data escape its roots."""

    data_dir: Path
    upload_dir: Path

    def source_for_record(self, record: dict) -> Path | None:
        stored = str(record.get("stored_name") or "")
        if stored.startswith("sample:"):
            candidate = self.data_dir / stored.split(":", 1)[1]
            return self._safe_existing(candidate, self.data_dir)
        candidate = self.upload_dir / Path(stored).name
        if self._safe_existing(candidate, self.upload_dir):
            return candidate
        fallback = self.data_dir / str(record.get("filename") or "")
        return self._safe_existing(fallback, self.data_dir)

    def cleanup_cancelled_upload(self, job: dict) -> None:
        stored = str(job.get("stored_name") or "")
        if not stored or job.get("preserve_upload") or stored.startswith("sample:"):
            return
        candidate = self.upload_dir / Path(stored).name
        if self._safe_existing(candidate, self.upload_dir, require_file=False):
            candidate.unlink(missing_ok=True)

    def referenced_uploads(self, records: Iterable[dict]) -> set[str]:
        """Collect upload names from records without touching the database."""

        return {
            Path(str(record.get("stored_name") or "")).name
            for record in records
            if record.get("stored_name")
            and not str(record["stored_name"]).startswith("sample:")
        }

    @staticmethod
    def _safe_existing(candidate: Path, root: Path, *, require_file: bool = True) -> Path | None:
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
            if not resolved.is_relative_to(root_resolved):
                return None
        except (OSError, RuntimeError):
            return None
        if require_file and not resolved.is_file():
            return None
        return resolved

