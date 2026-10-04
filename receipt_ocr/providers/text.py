"""Provider-neutral text recognition ports and the catalog-backed adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from contextlib import contextmanager
from contextvars import ContextVar

from ..domain.ocr import TextObservation
from . import catalog, paddle_runtime


class TextRecognizer(Protocol):
    def recognize(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]: ...
    def recognize_line(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]: ...
    def supports_line(self, backend: str) -> bool: ...
    def detect_boxes(self, image_path: str | Path, *, backend: str | None = None, model_variant: str | None = None): ...
    def recognize_seal(self, image_path: str | Path) -> list[TextObservation]: ...


class CatalogTextRecognizer:
    """Default adapter; model selection remains inside the provider catalog."""

    def recognize(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]:
        return catalog.recognize_text(image_path, backend=backend, **options)

    def recognize_line(self, image_path: str | Path, *, backend: str | None = None, **options) -> list[TextObservation]:
        if backend and not self.supports_line(backend):
            return self.recognize(image_path, backend=backend, **options)
        variant = options.pop("model_variant", None) or paddle_runtime.variant_of(backend) or "v6"
        return paddle_runtime.recognize_line(image_path, model_variant=variant, **options)

    def supports_line(self, backend: str) -> bool:
        return paddle_runtime.is_paddle_backend(backend) or paddle_runtime.is_seal_backend(backend)

    def detect_boxes(self, image_path, *, backend=None, model_variant=None):
        return paddle_runtime.detect_text_boxes(
            image_path, model_variant=model_variant or paddle_runtime.variant_of(backend) or "v6"
        )

    def recognize_seal(self, image_path):
        return paddle_runtime.recognize_seal_text(image_path)


def default_text_recognizer() -> TextRecognizer:
    return CatalogTextRecognizer()


_recognizer: ContextVar[TextRecognizer | None] = ContextVar("receipt_text_recognizer", default=None)


def current_text_recognizer() -> TextRecognizer:
    reader = _recognizer.get()
    return reader if reader is not None else default_text_recognizer()


@contextmanager
def text_recognizer_scope(reader: TextRecognizer):
    """Propagate the injected reader to nested OCR adapters for this run only."""
    token = _recognizer.set(reader)
    try:
        yield reader
    finally:
        _recognizer.reset(token)


__all__ = ["CatalogTextRecognizer", "TextRecognizer", "default_text_recognizer",
           "current_text_recognizer", "text_recognizer_scope"]
