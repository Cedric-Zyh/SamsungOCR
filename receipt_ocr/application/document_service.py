"""Recognize one stored document and assign its output artifacts and timestamps."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from receipt_ocr.storage.artifact_dedup import deduplicate_artifacts
from receipt_ocr.runtime.calls import capture_recognition_calls


@dataclass(frozen=True)
class DocumentRecognitionService:
    analyze: Callable
    preview_dir: Path
    artifact_dir: Path
    now_iso: Callable

    def recognize(self, source, *, filename, created_at=None, **options):
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError("原始图片不存在，请补充图片后重新识别")
        token = uuid4().hex
        preview_name = f"{token}.jpg"
        with capture_recognition_calls(source=source, artifact_dir=self.artifact_dir / token,
                                       artifact_url_prefix=f"/files/artifacts/{token}") as calls:
            result = self.analyze(
                source, self.preview_dir / preview_name,
                artifact_dir=self.artifact_dir / token,
                artifact_url_prefix=f"/files/artifacts/{token}",
                filename=filename, **options,
            )
        # Deduplication rewrites artifact URLs recursively, including the
        # transient call trace; no second copy of an input image is created.
        result["_recognition_calls"] = calls.snapshot()
        result = deduplicate_artifacts(result, self.artifact_dir / token,
                                       f"/files/artifacts/{token}")
        timestamp = self.now_iso()
        result.update(filename=filename, preview_url=f"/files/previews/{preview_name}",
                      created_at=created_at or timestamp, updated_at=timestamp)
        return result, preview_name
