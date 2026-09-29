"""Compatibility alias; implementation lives in audit.line_reads."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.audit.line_reads")
