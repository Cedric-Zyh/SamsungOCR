"""Compatibility alias; implementation lives in audit.color_confirmation."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.audit.color_confirmation")
