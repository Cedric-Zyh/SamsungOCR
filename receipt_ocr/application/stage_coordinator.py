"""Stage coordinator used by the application recognition service."""

from __future__ import annotations

from typing import Any

from ..recognition.seal.api import SealApiClient
from ..runtime.execution import recognition_run
from .pipeline import execute_stage, run_legacy


class ReceiptAnalyzer:
    """Register and dispatch recognition stages for one application run."""

    def __init__(self, seal_api: Any | None = None):
        self.seal_api = seal_api or SealApiClient()

    @recognition_run
    def analyze(self, image_path, preview_path=None, **options):
        return run_legacy(self, image_path, preview_path, **options)

    def run_stage(self, context, stage, request):
        return execute_stage(self, context, stage, request)

    def _recognize_receipt_date(self, *args, **kwargs):
        from receipt_ocr.recognition.date.api import recognize_receipt_date

        return recognize_receipt_date(*args, **kwargs)

    def _recognize_local_seals(self, *args, **kwargs):
        from receipt_ocr.recognition.seal.workflow import recognize_local_seals

        return recognize_local_seals(*args, **kwargs)


__all__ = ["ReceiptAnalyzer"]
