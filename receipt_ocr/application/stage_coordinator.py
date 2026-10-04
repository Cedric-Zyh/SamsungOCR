"""Stage coordinator used by the application recognition service."""

from __future__ import annotations

from typing import Any

from ..recognition.seal.api import SealApiClient
from ..providers.text import default_text_recognizer
from .pipeline import execute_stage
from .plans import run_configured


class ReceiptAnalyzer:
    """Register and dispatch recognition stages for one application run."""

    def __init__(self, seal_api: Any | None = None, *, text_recognizer=None):
        self.seal_api = seal_api if seal_api is not None else SealApiClient()
        self.text_recognizer = text_recognizer if text_recognizer is not None else default_text_recognizer()

    def analyze(self, image_path, preview_path=None, *, recognition_config=None, previous_fields=None, **options):
        return run_configured(self, image_path, preview_path, config=recognition_config,
                              previous_fields=previous_fields, **options)

    def run_stage(self, context, stage, request):
        return execute_stage(self, context, stage, request)

    def _recognize_receipt_date(self, *args, **kwargs):
        from receipt_ocr.recognition.date.api import recognize_receipt_date

        return recognize_receipt_date(*args, **kwargs)

    def _recognize_local_seals(self, *args, **kwargs):
        from receipt_ocr.recognition.seal.api import recognize_local_seals

        return recognize_local_seals(*args, **kwargs)


__all__ = ["ReceiptAnalyzer"]
