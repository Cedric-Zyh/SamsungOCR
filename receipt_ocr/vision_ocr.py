from __future__ import annotations

from pathlib import Path
from typing import Iterable

import Vision
from Foundation import NSURL
from Quartz import CGImageSourceCreateImageAtIndex, CGImageSourceCreateWithURL
from .ocr_types import TextObservation, observations_text


def recognize_text(
    image_path: str | Path,
    *,
    languages: Iterable[str] = ("zh-Hans", "en-US"),
    min_text_height: float = 0.005,
    fast: bool = False,
    custom_words: Iterable[str] = (),
    language_correction: bool = True,
) -> list[TextObservation]:
    """Run the macOS Vision text recognizer and return top-left normalized boxes."""
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    url = NSURL.fileURLWithPath_(str(path))
    source = CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise ValueError(f"无法读取图片: {path}")
    image = CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise ValueError(f"无法解码图片: {path}")

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelFast
        if fast
        else Vision.VNRequestTextRecognitionLevelAccurate
    )
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(language_correction)
    request.setMinimumTextHeight_(min_text_height)
    words = list(custom_words)
    if words:
        request.setCustomWords_(words)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError(f"Vision OCR 失败: {error}")

    output: list[TextObservation] = []
    for item in request.results() or []:
        candidates = item.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        box = item.boundingBox()
        output.append(
            TextObservation(
                text=str(candidate.string()).strip(),
                confidence=float(candidate.confidence()),
                x=float(box.origin.x),
                y=float(1.0 - box.origin.y - box.size.height),
                width=float(box.size.width),
                height=float(box.size.height),
            )
        )

    return sorted(output, key=lambda row: (round(row.y, 3), row.x))
