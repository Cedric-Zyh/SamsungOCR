from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from .ocr_types import TextObservation
from .execution import measure


# Intel/OpenMP may try to allocate a shared-memory control segment while
# Paddle is imported.  That fails in some sandboxed macOS launch contexts and
# aborts the whole batch before Python can report an item-level error.  Respect
# an explicit user setting, but use the file-backed fallback by default.
os.environ.setdefault("KMP_USE_SHM", "0")


MODEL_VARIANTS = {
    "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
    "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
}

_PIPELINES = {}
_LINE_RECOGNIZERS = {}
_PIPELINE_LOCK = threading.Lock()
_PREDICT_LOCK = threading.Lock()


@contextmanager
def _prediction_slot():
    # Keep the native predictor serialized, but expose waiting separately from
    # model loading and actual inference. Always release on a failed predict.
    with measure('paddle_queue_wait'):
        _PREDICT_LOCK.acquire()
    try:
        yield
    finally:
        _PREDICT_LOCK.release()


def server_max_side() -> int:
    """Bound Server-model page memory while keeping normalized coordinates."""
    try:
        return max(640, int(os.getenv("PADDLE_SERVER_MAX_SIDE", "1400")))
    except ValueError:
        return 1400


def paddle_model_home() -> Path:
    configured = os.getenv("PADDLE_PDX_CACHE_HOME") or os.getenv("PADDLE_MODEL_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    external = Path("/Volumes/SN770/OCR")
    if external.is_dir():
        return external
    return (Path.home() / ".paddlex").resolve()


def _pipeline(model_variant: str):
    if model_variant not in MODEL_VARIANTS:
        raise ValueError(f"未知 PaddleOCR 模型规格: {model_variant}")
    if model_variant in _PIPELINES:
        return _PIPELINES[model_variant]
    with _PIPELINE_LOCK:
        if model_variant in _PIPELINES:
            return _PIPELINES[model_variant]
        model_home = paddle_model_home()
        model_home.mkdir(parents=True, exist_ok=True)
        # PaddleX reads these during import, so they must be set first.
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(model_home))
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR

        detection_model, recognition_model = MODEL_VARIANTS[model_variant]
        _PIPELINES[model_variant] = PaddleOCR(
            text_detection_model_name=detection_model,
            text_recognition_model_name=recognition_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
    return _PIPELINES[model_variant]


def _line_recognizer(model_variant: str):
    """Load Paddle's recognition-only model for a pre-cropped text line."""
    if model_variant not in MODEL_VARIANTS:
        raise ValueError(f"未知 PaddleOCR 模型规格: {model_variant}")
    if model_variant in _LINE_RECOGNIZERS:
        return _LINE_RECOGNIZERS[model_variant]
    with _PIPELINE_LOCK:
        if model_variant in _LINE_RECOGNIZERS:
            return _LINE_RECOGNIZERS[model_variant]
        model_home = paddle_model_home()
        model_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(model_home))
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import TextRecognition

        recognition_model = MODEL_VARIANTS[model_variant][1]
        _LINE_RECOGNIZERS[model_variant] = TextRecognition(
            model_name=recognition_model,
            device="cpu",
        )
    return _LINE_RECOGNIZERS[model_variant]


def recognize_line(
    image_path: str | Path,
    *,
    model_variant: str = "mobile",
) -> list[TextObservation]:
    """Recognize one already-cropped line without running text detection.

    Handwritten dates often contain adjacent repeated digits.  The regular OCR
    detector can merge or clip that line before recognition, so this path feeds
    the whole date line directly to the same PP-OCR recognition model.
    """
    from .recognition_scope import provider_allowed
    if not provider_allowed('paddle_server' if model_variant == 'server' else 'paddle'):
        return []
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with _prediction_slot():
        with measure('paddle_model_setup'):
            recognizer = _line_recognizer(model_variant)
        with measure('paddle_line_inference'):
            results = list(recognizer.predict(input=str(path)))
    output: list[TextObservation] = []
    for result in results:
        text = str(result.get("rec_text", "")).strip()
        score = float(result.get("rec_score", 0.0) or 0.0)
        if not text:
            continue
        output.append(TextObservation(
            text=text,
            confidence=max(0.0, min(1.0, score)),
            x=0.0,
            y=0.0,
            width=1.0,
            height=1.0,
        ))
    return output


def recognize_text(
    image_path: str | Path,
    *,
    languages: Iterable[str] = ("zh-Hans", "en-US"),
    min_text_height: float = 0.005,
    fast: bool = False,
    custom_words: Iterable[str] = (),
    language_correction: bool = True,
    model_variant: str = "mobile",
) -> list[TextObservation]:
    """Run local PP-OCRv5 and return top-left normalized text boxes."""
    del languages, fast, custom_words, language_correction  # Common backend interface.
    from .recognition_scope import provider_allowed
    if not provider_allowed('paddle_server' if model_variant == 'server' else 'paddle'):
        return []
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    from PIL import Image

    with Image.open(path) as image:
        image_width, image_height = image.size
    if image_width <= 0 or image_height <= 0:
        raise ValueError(f"无法读取图片尺寸: {path}")

    output: list[TextObservation] = []
    inference_path = path
    inference_width, inference_height = image_width, image_height
    temporary: tempfile.TemporaryDirectory | None = None
    if model_variant == "server" and max(image_width, image_height) > server_max_side():
        # PP-OCR Server can otherwise exceed local memory on 3k–6k scanner
        # pages.  Normalized box coordinates are scale invariant, so a bounded
        # inference copy preserves every downstream fixed-region calculation.
        temporary = tempfile.TemporaryDirectory(prefix="receipt-paddle-server-")
        inference_path = Path(temporary.name) / "input.jpg"
        with Image.open(path) as source:
            scaled = source.convert("RGB")
            scaled.thumbnail(
                (server_max_side(), server_max_side()),
                Image.Resampling.LANCZOS,
            )
            inference_width, inference_height = scaled.size
            scaled.save(inference_path, format="JPEG", quality=94)
    # The Paddle pipeline object is reused to avoid repeated model loading, while
    # inference is serialized because the native predictor is not thread-safe.
    try:
        with _prediction_slot():
            with measure('paddle_model_setup'):
                pipeline = _pipeline(model_variant)
            with measure('paddle_text_inference'):
                results = list(pipeline.predict(input=str(inference_path)))
    finally:
        if temporary is not None:
            temporary.cleanup()
    for result in results:
        texts = result.get("rec_texts", [])
        scores = result.get("rec_scores", [])
        polygons = result.get("rec_polys", [])
        for text, score, polygon in zip(texts, scores, polygons):
            points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
            if not points:
                continue
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            normalized_height = max(0.0, (y2 - y1) / inference_height)
            value = str(text).strip()
            if not value or normalized_height < min_text_height:
                continue
            output.append(TextObservation(
                text=value,
                confidence=max(0.0, min(1.0, float(score))),
                x=max(0.0, x1 / inference_width),
                y=max(0.0, y1 / inference_height),
                width=min(1.0, max(0.0, (x2 - x1) / inference_width)),
                height=min(1.0, normalized_height),
            ))
    return sorted(output, key=lambda row: (round(row.y, 3), row.x))
