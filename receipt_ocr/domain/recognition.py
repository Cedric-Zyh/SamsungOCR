"""Value objects exchanged by recognition application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecognitionOptions:
    """Validated options for one recognition request."""

    ocr_backend: str
    seal_recognition_mode: str
    recognition_config: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ocr_backend": self.ocr_backend,
            "seal_recognition_mode": self.seal_recognition_mode,
            "recognition_config": self.recognition_config,
        }

