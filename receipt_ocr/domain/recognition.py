"""Value objects exchanged by recognition application services."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any


def options_with_current_settings(options: dict, settings: dict) -> dict:
    """Snapshot the shared plan while retaining job-specific source context."""
    plan = deepcopy(settings['recognition_config'])
    plan['acceptance'] = deepcopy(settings['acceptance_policy'])
    return {**options, 'recognition_config': plan,
            'ocr_backend': 'auto', 'seal_recognition_mode': 'local'}


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

