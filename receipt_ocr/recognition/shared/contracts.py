"""Contracts shared by date and seal recognition features."""
from receipt_ocr.domain.ocr import TextObservation, observations_text
from receipt_ocr.application.requests import StageRequest
from receipt_ocr.imaging.contracts import SealRegion

__all__ = ["TextObservation", "observations_text", "StageRequest", "SealRegion"]
