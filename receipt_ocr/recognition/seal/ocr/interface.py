"""Provider-neutral OCR interface used by the seal pipeline.

The seal stages only ask for text from an image.  Which engine, model tier, or
transport supplies that text belongs here, at the edge of the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.providers import catalog, paddle_runtime
from ..contracts import OcrRead


class OcrReader:
    """Small provider-neutral facade for image and line recognition."""

    def read(
        self,
        image: str | Path,
        *,
        provider: str | None = None,
        backend: str | None = None,
        min_text_height: float = 0.012,
    ) -> list[TextObservation]:
        selected = provider or backend or ""
        if paddle_runtime.is_seal_backend(selected):
            return paddle_runtime.recognize_seal_text(image)
        return catalog.recognize_text(
            image,
            backend=selected,
            min_text_height=min_text_height,
        )

    def detect_boxes(
        self,
        image: str | Path,
        *,
        provider: str | None = None,
        model_variant: str | None = None,
    ):
        """Detect text boxes for orientation decisions through this boundary."""
        variant = model_variant or paddle_runtime.variant_of(provider) or "v6"
        return paddle_runtime.detect_text_boxes(image, model_variant=variant)

    def read_line(
        self,
        image: str | Path,
        *,
        provider: str | None = None,
        model_variant: str | None = None,
    ) -> list[TextObservation]:
        if provider and not (paddle_runtime.is_paddle_backend(provider) or paddle_runtime.is_seal_backend(provider)):
            return self.read(image, provider=provider)
        variant = model_variant or paddle_runtime.variant_of(provider) or "v6"
        return paddle_runtime.recognize_line(image, model_variant=variant)


_DEFAULT_READER = OcrReader()


def detect_boxes(
    image: str | Path,
    *,
    provider: str | None = None,
    model_variant: str | None = None,
):
    """Detect text boxes through the shared OCR boundary."""
    return _DEFAULT_READER.detect_boxes(
        image, provider=provider, model_variant=model_variant
    )


def read_text(
    image: str | Path,
    *,
    provider: str | None = None,
    backend: str | None = None,
    min_text_height: float = 0.012,
):
    """Read text through the shared OCR boundary."""

    return _DEFAULT_READER.read(
        image,
        provider=provider,
        backend=backend,
        min_text_height=min_text_height,
    )


def read_line(
    image: str | Path,
    *,
    provider: str | None = None,
    model_variant: str | None = None,
):
    """Read one prepared line through the shared OCR boundary."""

    return _DEFAULT_READER.read_line(
        image,
        provider=provider,
        model_variant=model_variant,
    )


def normalize_reads(
    rows: list[TextObservation],
    *,
    provider: str,
    variant: str = "",
    source: str | Path | None = None,
) -> list[OcrRead]:
    """Convert provider observations to the common result contract."""

    source_path = Path(source) if source is not None else None
    return [
        OcrRead(
            text=str(row.text or ""),
            confidence=getattr(row, "confidence", None),
            provider=provider,
            variant=variant,
            source=source_path,
        )
        for row in rows
        if getattr(row, "text", "")
    ]


__all__ = ["OcrRead", "OcrReader", "normalize_reads", "detect_boxes", "read_line", "read_text"]
