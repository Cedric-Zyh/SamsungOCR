"""Public seal recognition API."""

from .pipeline import _recognize_local_seals as recognize_local_seals
from .providers.api import SealApiClient, SEAL_RECOGNITION_MODES, resolve_seal_recognition_mode

__all__ = ["recognize_local_seals", "SealApiClient", "SEAL_RECOGNITION_MODES", "resolve_seal_recognition_mode"]
