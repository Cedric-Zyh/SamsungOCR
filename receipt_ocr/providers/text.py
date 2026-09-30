"""Provider-neutral text recognition ports and the catalog-backed adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain.ocr import TextObservation
from . import catalog, paddle_runtime


class TextRecognizer(Protocol):
    def recognize(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]: ...
    def recognize_line(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]: ...
    def supports_line(self, backend: str) -> bool: ...


class CatalogTextRecognizer:
    """Default adapter; model selection remains inside the provider catalog."""

    def recognize(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]:
        return catalog.recognize_text(image_path, backend=backend, **options)

    def recognize_line(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]:
        variant = paddle_runtime.variant_of(backend) or "v6"
        return paddle_runtime.recognize_line(image_path, model_variant=variant, **options)

    def supports_line(self, backend: str) -> bool:
        return paddle_runtime.is_paddle_backend(backend)


def default_text_recognizer() -> TextRecognizer:
    return CatalogTextRecognizer()


__all__ = ["CatalogTextRecognizer", "TextRecognizer", "default_text_recognizer"]
