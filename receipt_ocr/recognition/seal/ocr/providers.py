"""OCR provider profiles for seal recognition.

Profiles describe capabilities only. The seal pipeline asks a reader to read
prepared variants and keeps the selected profile in the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .interface import OcrReader


@dataclass(frozen=True)
class OcrProfile:
    id: str
    label: str
    supports_lines: bool = False
    supports_shape_inputs: bool = False


PROFILES = {
    "paddle": OcrProfile("paddle", "Paddle OCR", supports_lines=True),
    "paddle_v6": OcrProfile("paddle_v6", "Paddle OCR v6", supports_lines=True),
    "paddle_seal": OcrProfile(
        "paddle_seal", "Paddle SealOCR", supports_lines=True, supports_shape_inputs=True
    ),
    "vision": OcrProfile("vision", "Vision OCR"),
    "qingtong": OcrProfile("qingtong", "清瞳 OCR"),
    "danzhengtong": OcrProfile("danzhengtong", "单证通 OCR"),
}


def profile_for(provider: str | None) -> OcrProfile:
    key = str(provider or "paddle").strip().lower()
    return PROFILES.get(key, OcrProfile(key, key or "OCR"))


def reader_for(_provider: str | None = None) -> OcrReader:
    """Return the shared reader facade for a provider profile."""

    return OcrReader()


__all__ = ["OcrProfile", "PROFILES", "profile_for", "reader_for"]
