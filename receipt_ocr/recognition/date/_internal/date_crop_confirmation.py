"""Compatibility alias; implementation lives in audit.cross_model."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.audit.cross_model")
