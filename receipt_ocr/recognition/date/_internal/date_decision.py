"""Compatibility alias; implementation lives in postprocess.state."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.postprocess.state")
