"""Stamp orientation: models."""

import os
import threading


MODEL_NAME = "PP-LCNet_x0_25_textline_ori"

DOC_ORIENTATION_MODEL_NAME = "PP-LCNet_x1_0_doc_ori"

DOC_ORIENTATION_PROVIDER = "paddle_doc_ori"

_classifier = None

_doc_classifier = None

_lock = threading.Lock()

def classify_doc_orientation(image_path):
    """Return Paddle's four-way document orientation prediction for one crop."""
    global _doc_classifier
    from receipt_ocr.providers.paddle_runtime import paddle_model_home, _prediction_slot
    from receipt_ocr.runtime.scope import provider_allowed

    if not provider_allowed(DOC_ORIENTATION_PROVIDER):
        return {"angle": None, "confidence": 0.0, "model": DOC_ORIENTATION_MODEL_NAME}
    with _prediction_slot(), _lock:
        if _doc_classifier is None:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paddle_model_home()))
            os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import DocImgOrientationClassification

            # Use the same native Paddle weights as the verified classifier;
            # the page OCR engine may request a separate ONNX model download.
            _doc_classifier = DocImgOrientationClassification(
                model_name=DOC_ORIENTATION_MODEL_NAME,
                device="cpu",
            )
        predictions = list(_doc_classifier.predict(input=str(image_path), batch_size=1))
    if not predictions:
        return {"angle": None, "confidence": 0.0, "model": DOC_ORIENTATION_MODEL_NAME}
    prediction = predictions[0]
    labels = prediction.get("label_names", [])
    scores = prediction.get("scores", [])
    try:
        angle = int(str(labels[0]))
    except (IndexError, TypeError, ValueError):
        angle = None
    try:
        confidence = float(scores[0])
    except (IndexError, TypeError, ValueError):
        confidence = 0.0
    if angle not in (0, 90, 180, 270):
        angle = None
    return {
        "model": DOC_ORIENTATION_MODEL_NAME,
        "angle": angle,
        "confidence": round(confidence, 4),
    }

def classify_lines(lines):
    """Load the small orientation model once; images never leave the machine."""
    global _classifier
    from receipt_ocr.providers.paddle_runtime import paddle_model_home, _prediction_slot, paddle_engine

    with _prediction_slot(), _lock:
        if _classifier is None:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paddle_model_home()))
            os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import TextLineOrientationClassification

            engine = paddle_engine()
            _classifier = TextLineOrientationClassification(
                model_name=MODEL_NAME, device="cpu",
                **({"engine": engine} if engine else {}),
            )
        return list(_classifier.predict(input=lines, batch_size=1))
