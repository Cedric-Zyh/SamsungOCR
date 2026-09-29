"""Resolve and validate recognition options at the application boundary."""

from __future__ import annotations

import json
from typing import Any

from ..domain import RecognitionOptions
from ..providers.catalog import resolve_backend
from .plans import validate_config
from receipt_ocr.recognition.seal.api import resolve_seal_recognition_mode


def _decode_config(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return json.loads(value or "null")


def resolve_recognition_options(*, recognition_config: Any = None,
                                ocr_backend: Any = None,
                                seal_recognition_mode: Any = None,
                                api_enabled: bool = True) -> RecognitionOptions:
    """Return one validated option object for HTTP, queue, and retry flows."""

    return RecognitionOptions(
        ocr_backend=resolve_backend(str(ocr_backend or "auto")),
        seal_recognition_mode=resolve_seal_recognition_mode(seal_recognition_mode),
        recognition_config=validate_config(
            _decode_config(recognition_config),
            api_enabled=api_enabled,
        ),
    )

