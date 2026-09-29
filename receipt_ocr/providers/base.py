"""OCR adapter contract, independent from model initialization and UI state."""

from pathlib import Path
from typing import Protocol

from ..domain.ocr import TextObservation


class TextOcrProvider(Protocol):
    def recognize(self, image_path: str | Path, **options) -> list[TextObservation]:
        """Read one image. Cropping and evidence interpretation belong to stages."""
        ...
