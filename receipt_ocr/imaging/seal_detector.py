"""Local inference for the delivered single-class YOLO11s stamp detector.

The model locates whole stamps. It does not classify their shapes or read text.
Preprocessing and thresholds match the delivery's Detector implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import perf_counter

import cv2
import numpy as np

from receipt_ocr.runtime.calls import recognition_call, record_model_initialization
from receipt_ocr.runtime.seal_detector_model import seal_detector_model_path


INPUT_SIZE = 640
CONF_THRESHOLD = 0.55
IOU_THRESHOLD = 0.1
MAX_DETECTIONS = 300


@dataclass(frozen=True)
class SealDetection:
    xyxy: tuple[int, int, int, int]
    confidence: float


def _preprocess(image: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
    if image.ndim != 3 or image.shape[2] != 3 or not image.size:
        raise ValueError("印章检测需要非空 BGR 图片")
    height, width = image.shape[:2]
    scale = min(INPUT_SIZE / width, INPUT_SIZE / height)
    resized_width, resized_height = round(width * scale), round(height * scale)
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    left = (INPUT_SIZE - resized_width) // 2
    top = (INPUT_SIZE - resized_height) // 2
    canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.uint8)
    canvas[top:top + resized_height, left:left + resized_width] = resized
    tensor = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.ascontiguousarray(tensor.transpose(2, 0, 1)[None]), scale, (left, top)


def _postprocess(output: np.ndarray, image_shape: tuple[int, int], scale: float,
                 padding: tuple[int, int]) -> list[SealDetection]:
    # The bundled model outputs [1, 5, 8400]: cx, cy, w, h, seal score.
    if output.ndim != 3 or output.shape[0] != 1:
        raise ValueError(f"印章检测模型输出形状不兼容: {output.shape}")
    predictions = output[0]
    if predictions.shape[0] == 5:
        predictions = predictions.T
    elif predictions.shape[1] != 5:
        raise ValueError(f"印章检测模型必须为单类别: {output.shape}")
    valid = (np.isfinite(predictions).all(axis=1)
             & (predictions[:, 4] > CONF_THRESHOLD)
             & (predictions[:, 2] > 0) & (predictions[:, 3] > 0))
    predictions = predictions[valid]
    if not len(predictions):
        return []
    boxes = predictions[:, :4].copy()
    boxes[:, :2] -= boxes[:, 2:] / 2
    scores = predictions[:, 4]
    indices = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), CONF_THRESHOLD,
                               IOU_THRESHOLD, top_k=MAX_DETECTIONS)
    if indices is None or not len(indices):
        return []
    height, width = image_shape
    left, top = padding
    results = []
    for index in np.asarray(indices).reshape(-1):
        x, y, w, h = boxes[index]
        x1 = round(float(np.clip((x - left) / scale, 0, width)))
        y1 = round(float(np.clip((y - top) / scale, 0, height)))
        x2 = round(float(np.clip((x + w - left) / scale, 0, width)))
        y2 = round(float(np.clip((y + h - top) / scale, 0, height)))
        if x2 > x1 and y2 > y1:
            results.append(SealDetection((x1, y1, x2, y2), float(scores[index])))
    return results


class SealDetector:
    def __init__(self, model_path: Path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, INPUT_SIZE, INPUT_SIZE] or not outputs:
            raise ValueError("印章检测模型输入必须为 [1, 3, 640, 640]")
        self.input_name, self.output_name = inputs[0].name, outputs[0].name

    def predict(self, image: np.ndarray) -> list[SealDetection]:
        tensor, scale, padding = _preprocess(image)
        with recognition_call("seal_detector", "印章定位", image):
            output = self.session.run([self.output_name], {self.input_name: tensor})[0]
            return _postprocess(output, image.shape[:2], scale, padding)


_lock = Lock()
_detector: SealDetector | None = None


def load_seal_detector() -> SealDetector:
    global _detector
    with _lock:
        if _detector is None:
            started = perf_counter()
            try:
                _detector = SealDetector(seal_detector_model_path())
            finally:
                record_model_initialization(started, perf_counter())
        return _detector


def seal_detector_loaded() -> bool:
    with _lock:
        return _detector is not None


def release_seal_detector() -> None:
    global _detector
    with _lock:
        _detector = None


def detect_seal_boxes(image: np.ndarray) -> list[SealDetection]:
    return load_seal_detector().predict(image)
