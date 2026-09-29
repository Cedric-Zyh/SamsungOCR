"""Compatibility alias; implementation lives in ocr.regions."""
from importlib import import_module
import sys

sys.modules[__name__] = import_module("receipt_ocr.recognition.date.ocr.regions")
