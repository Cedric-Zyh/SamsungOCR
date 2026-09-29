"""Compatibility alias; implementation lives in preprocess.line_views."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.preprocess.line_views")
