from __future__ import annotations

from .recognition_progress import model_operation

import os
import math
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


# PP-OCRv6 ("multi_PP-OCRv6_small" in CnOCR terms) needs paddleocr>=3.7.0.
# Each entry is a (detection, recognition) pair; a variant is only usable when
# its weights exist for the installed PaddleOCR version.
MODEL_VARIANTS = {
    "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
    "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
    "v6": ("PP-OCRv6_small_det", "PP-OCRv6_small_rec"),
}

# A model variant is reached through exactly one request-scoped provider id.
# ``recognition_scope.provider_scope`` gates every OCR call by the backend id
# the recognition plan selected, so these ids must stay in step with
# ``ocr_backends.BACKEND_LABELS`` / ``backend_catalog``. Missing one turns the
# stage into a silent empty result instead of an error.
VARIANT_PROVIDERS = {
    "mobile": "paddle",
    "server": "paddle_server",
    "v6": "paddle_v6",
}
_PROVIDER_VARIANTS = {provider: variant for variant, provider in VARIANT_PROVIDERS.items()}

PADDLE_BACKENDS = frozenset(VARIANT_PROVIDERS.values())
SEAL_BACKENDS = frozenset({"paddle_seal"})

# Tiers light enough to load a second recognition-only predictor next to an
# already-loaded page pipeline. The Server tier is deliberately excluded.
LIGHTWEIGHT_VARIANTS = frozenset({"mobile", "v6"})


def is_paddle_backend(name: str | None) -> bool:
    """True when a backend id is served by one of the local PaddleOCR models."""
    return str(name or "").strip().lower() in PADDLE_BACKENDS


def is_seal_backend(name: str | None) -> bool:
    """True for the stamp-only OCR route."""
    return str(name or "").strip().lower() in SEAL_BACKENDS


def provider_of(model_variant: str) -> str:
    """Provider id used by the request scope for a model variant."""
    return VARIANT_PROVIDERS.get(model_variant, VARIANT_PROVIDERS["mobile"])


def variant_of(backend: str | None) -> str | None:
    """Model variant behind a backend id, or ``None`` for a non-Paddle backend."""
    return _PROVIDER_VARIANTS.get(str(backend or "").strip().lower())


def is_lightweight_backend(value: str | None) -> bool:
    """True for the small local Paddle tiers (Mobile / PP-OCRv6 Small).

    Accepts either a backend id (``paddle``, ``paddle_v6``) or a backend label
    (``PaddleOCR PP-OCRv6 Small``) because older date artifacts persist the
    label text rather than the id.
    """
    text = str(value or "").strip().lower()
    if variant_of(text) in LIGHTWEIGHT_VARIANTS:
        return True
    if not text or "server" in text:
        return False
    return any(token in text for token in ("mobile", "small", "tiny"))


# PP-OCR runs several times faster through the ONNX Runtime provider than
# through the default CPU kernels, with identical text on every sample the two
# have been compared against.  The first use converts each model once and
# caches the result next to the PaddleX weights, so that cost is paid per
# machine rather than per receipt.  ``PADDLE_OCR_ENGINE`` accepts any other
# PaddleX engine id; ``paddle`` / ``off`` / ``cpu`` restore the default kernels.
# The setting is silently ignored when onnxruntime is not installed, so a
# machine without it keeps working unchanged.
DEFAULT_ENGINE = "onnxruntime"
_DISABLED_ENGINES = frozenset(
    {"", "0", "off", "no", "false", "none", "paddle", "cpu", "default"}
)

_PIPELINES = {}
_LINE_RECOGNIZERS = {}
_SEAL_DETECTORS = {}
_PIPELINE_LOCK = threading.Lock()
_PREDICT_LOCK = threading.Lock()
_ENGINE_STATE = {"engine": None}


def paddle_engine() -> str | None:
    """PaddleX engine id for local PP-OCR, or ``None`` for the default kernels."""
    configured = os.getenv("PADDLE_OCR_ENGINE")
    requested = DEFAULT_ENGINE if configured is None else configured.strip()
    if requested.lower() in _DISABLED_ENGINES:
        return None
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return None
    return requested


def _engine_kwargs() -> dict:
    """Provider arguments, dropping cached predictors when the engine changes.

    Tests flip ``PADDLE_OCR_ENGINE`` between runs of one process, so the
    per-variant caches are cleared whenever the resolved engine differs from the
    one the cached predictors were built with.  A stable setting never clears.
    """
    engine = paddle_engine()
    if _ENGINE_STATE["engine"] != engine:
        _ENGINE_STATE["engine"] = engine
        _PIPELINES.clear()
        _LINE_RECOGNIZERS.clear()
        _SEAL_DETECTORS.clear()
    return {"engine": engine} if engine else {}


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
    engine_kwargs = _engine_kwargs()
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
            **engine_kwargs,
        )
    return _PIPELINES[model_variant]


def _line_recognizer(model_variant: str):
    """Load Paddle's recognition-only model for a pre-cropped text line."""
    if model_variant not in MODEL_VARIANTS:
        raise ValueError(f"未知 PaddleOCR 模型规格: {model_variant}")
    engine_kwargs = _engine_kwargs()
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
            **engine_kwargs,
        )
    return _LINE_RECOGNIZERS[model_variant]


def _seal_pipeline():
    """Use Paddle's seal OCR pipeline, including curved-text rectification."""
    if "seal" in _SEAL_DETECTORS:
        return _SEAL_DETECTORS["seal"]
    with _PIPELINE_LOCK:
        if "seal" in _SEAL_DETECTORS:
            return _SEAL_DETECTORS["seal"]
        model_home = paddle_model_home()
        model_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(model_home))
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR

        # This is the SealOCR subpipeline from Paddle's seal_recognition
        # configuration. Layout detection is unnecessary for pre-cropped seals.
        # Crucially, text_type=seal rectifies polygons instead of taking an
        # axis-aligned bounding box of the circular text.
        config = {
            "pipeline_name": "OCR",
            "text_type": "seal",
            "use_doc_preprocessor": False,
            "use_textline_orientation": False,
            "SubModules": {
                "TextDetection": {
                    "module_name": "seal_text_detection",
                    "model_name": "PP-OCRv4_mobile_seal_det",
                    "limit_side_len": 736, "limit_type": "min",
                    "thresh": 0.2, "box_thresh": 0.6, "unclip_ratio": 0.5,
                },
                "TextRecognition": {
                    "module_name": "text_recognition",
                    "model_name": "PP-OCRv6_small_rec",
                    "batch_size": 1, "score_thresh": 0,
                },
            },
        }
        _SEAL_DETECTORS["seal"] = PaddleOCR(
            paddlex_config=config,
            text_detection_model_name="PP-OCRv4_mobile_seal_det",
            text_recognition_model_name="PP-OCRv6_small_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
    return _SEAL_DETECTORS["seal"]


def recognize_seal_text(image_path: str | Path) -> list[TextObservation]:
    """Recognize one seal crop; load/predict errors must remain visible."""
    from .recognition_scope import provider_allowed
    from PIL import Image

    if not provider_allowed("paddle_seal"):
        return []
    path = Path(image_path).expanduser().resolve()
    with Image.open(path) as image:
        width, height = image.size
    with model_operation("paddle_seal", "识别印章区域"), _prediction_slot():
        with measure("paddle_seal_model_setup"):
            pipeline = _seal_pipeline()
        with measure("paddle_seal_inference"):
            results = list(pipeline.predict(input=str(path)))
    output = []
    for result in results:
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        for text, score, poly in zip(
            result.get("rec_texts", []),
            result.get("rec_scores", []),
            result.get("rec_polys", []),
        ):
            value = str(text).strip()
            points = poly.tolist() if hasattr(poly, "tolist") else poly
            if not value or not points:
                continue
            xs, ys = zip(*points)
            output.append(TextObservation(
                text=value, confidence=max(0.0, min(1.0, float(score))),
                x=max(0.0, min(xs) / width),
                y=max(0.0, min(ys) / height),
                width=min(1.0, (max(xs) - min(xs)) / width),
                height=min(1.0, (max(ys) - min(ys)) / height),
            ))
    # Preserve the seal pipeline's polygon order, not page reading order.
    return output


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
    if not provider_allowed(provider_of(model_variant)):
        return []
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with model_operation(provider_of(model_variant), '识别文字行'), _prediction_slot():
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
    """Run a local PP-OCR pipeline and return top-left normalized text boxes."""
    del languages, fast, custom_words, language_correction  # Common backend interface.
    from .recognition_scope import provider_allowed
    if not provider_allowed(provider_of(model_variant)):
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
        with model_operation(provider_of(model_variant), '文字检测与识别'), _prediction_slot():
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


def detect_text_boxes(
    image_path: str | Path,
    *,
    model_variant: str = "mobile",
) -> list[dict]:
    """Return Paddle text polygons, recognition text and their line angles.

    The regular OCR API intentionally exposes only normalized axis-aligned
    boxes because most fields do not need polygon geometry.  Seal orientation
    is different: a round stamp's ``用章``/``专用章`` line is a useful local
    direction anchor, so keep the original four-point polygon here.
    """
    from .recognition_scope import provider_allowed

    provider = provider_of(model_variant)
    if not provider_allowed(provider):
        return []
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        return []

    with _prediction_slot():
        with measure("paddle_text_box_setup"):
            pipeline = _pipeline(model_variant)
        with measure("paddle_text_box_inference"):
            results = list(pipeline.predict(input=str(path)))

    output: list[dict] = []
    for result in results:
        for text, score, polygon in zip(
            result.get("rec_texts", []),
            result.get("rec_scores", []),
            result.get("rec_polys", []),
        ):
            points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
            if not points or len(points) < 4:
                continue
            points = [[float(point[0]), float(point[1])] for point in points]
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            # Paddle's polygon follows the text line, but its starting corner
            # is not an API guarantee.  Choose the long edge and normalize its
            # direction to [-90, 90), which is stable for either point order.
            edges = [
                (points[(index + 1) % len(points)][0] - points[index][0],
                 points[(index + 1) % len(points)][1] - points[index][1])
                for index in range(len(points))
            ]
            dx, dy = max(edges, key=lambda edge: edge[0] * edge[0] + edge[1] * edge[1])
            angle = math.degrees(math.atan2(dy, dx))
            while angle >= 90:
                angle -= 180
            while angle < -90:
                angle += 180
            value = str(text).strip()
            if not value:
                continue
            output.append({
                "text": value,
                "confidence": max(0.0, min(1.0, float(score))),
                "points": points,
                "angle": angle,
                "x": min(xs) / width,
                "y": min(ys) / height,
                "width": (max(xs) - min(xs)) / width,
                "height": (max(ys) - min(ys)) / height,
            })
    return output
