"""Compatibility alias; implementation lives in audit.low_confidence."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.audit.low_confidence")
