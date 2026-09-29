"""Compatibility alias; implementation lives in audit.slot_output."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.audit.slot_output")
