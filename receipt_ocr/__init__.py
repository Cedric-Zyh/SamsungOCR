"""三星回单识别核心包。

The package surface intentionally contains only application entry points. OCR
providers, stages, and domain rules are imported from their explicit packages.
"""

from .application.analyzer import ReceiptAnalyzer
from .application.recognition_service import RecognitionService

__all__ = ["ReceiptAnalyzer", "RecognitionService"]
