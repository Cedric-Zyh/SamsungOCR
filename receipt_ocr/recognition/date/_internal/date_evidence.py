"""Compatibility alias for the historical evidence barrel."""
from importlib import import_module
import sys
sys.modules[__name__] = import_module("receipt_ocr.recognition.date._internal.evidence_exports")
