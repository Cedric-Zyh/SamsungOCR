"""OCR adapters and read policies for seal recognition."""

from .interface import OcrRead, OcrReader
from .providers import OcrProfile, profile_for, reader_for

__all__ = ["OcrProfile", "OcrRead", "OcrReader", "profile_for", "reader_for"]
