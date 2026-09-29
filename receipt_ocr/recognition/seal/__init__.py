"""Seal recognition public boundaries."""

from .api import SealApiClient, SEAL_RECOGNITION_MODES, recognize_local_seals, resolve_seal_recognition_mode
from .contracts import OcrRead, PreparedSeal, PreparedVariant, SealResult
from .decision import *  # noqa: F401,F403

__all__ = [
    "OcrRead",
    "PreparedSeal",
    "PreparedVariant",
    "SealApiClient",
    "SEAL_RECOGNITION_MODES",
    "SealResult",
    "recognize_local_seals",
    "resolve_seal_recognition_mode",
]
