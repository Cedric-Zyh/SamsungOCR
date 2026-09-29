"""Contracts shared by date and seal recognition features."""
from receipt_ocr.domain.ocr import TextObservation, observations_text
from receipt_ocr.domain.requests import StageRequest
from receipt_ocr.imaging.processing import SealRegion

__all__ = ["TextObservation", "observations_text", "StageRequest", "SealRegion"]
