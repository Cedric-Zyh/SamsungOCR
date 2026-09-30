from types import SimpleNamespace

import cv2
import numpy as np

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess.orientation import (
    choose_rectangular_stamp_angle,
    prepare_rectangular_stamp,
)


def test_rectangular_rows_are_combined_in_visual_order():
    rows = [
        TextObservation("2310637", 0.99, 0.0, 0.35, 1.0, 0.5),
        TextObservation("三星电子维修中心", 0.99, 0.0, 0.05, 1.0, 0.2),
    ]

    assert pipeline._combine_rectangular_body_text(rows) == "三星电子维修中心2310637"


def test_rectangular_orientation_uses_widest_horizontal_row():
    result = choose_rectangular_stamp_angle([
        {
            "text": "三星电子维修中心",
            "confidence": 0.99,
            "angle": -3.2,
            "points": [[120, 80], [880, 40], [890, 180], [130, 220]],
        },
        {
            "text": "2310637",
            "confidence": 0.99,
            "angle": -5.1,
            "points": [[90, 230], [930, 150], [950, 450], [110, 530]],
        },
    ])

    assert result["anchor_text"] == "2310637"
    assert result["applied_rotation"] == -5.1


def test_rectangular_orientation_adds_margin_and_rotates(monkeypatch, tmp_path):
    source = tmp_path / "color.png"
    destination = tmp_path / "oriented.png"
    cv2.imwrite(str(source), np.full((100, 200, 3), 255, dtype=np.uint8))
    monkeypatch.setattr(
        "receipt_ocr.recognition.seal.ocr.interface.detect_boxes",
        lambda *args, **kwargs: [{
            "text": "2310637",
            "confidence": 0.99,
            "angle": -5.0,
            "points": [[10, 45], [190, 30], [190, 75], [10, 90]],
        }],
    )

    oriented, decision = prepare_rectangular_stamp(source, destination, model_variant="v6")

    assert oriented == destination
    assert decision["applied_rotation"] == -5.0
    assert decision["padding"] == 12
    output = cv2.imread(str(destination))
    assert output is not None
    assert output.shape[0] > 100 and output.shape[1] > 200


def test_rectangular_merge_ignores_low_confidence_fragment_but_keeps_it_as_raw_evidence():
    rows = [
        TextObservation("三星电子维修中心", 0.99, 0.0, 0.05, 1.0, 0.2),
        TextObservation("nelah", 0.42, 0.0, 0.14, 0.02, 0.1),
        TextObservation("2310637", 0.99, 0.0, 0.35, 1.0, 0.5),
    ]

    assert pipeline._combine_rectangular_body_text(rows) == "三星电子维修中心2310637"


def test_rectangular_combined_candidate_is_added_before_matching(monkeypatch, tmp_path):
    body_input = {
        "id": "seal-0:color",
        "channel": "body",
        "image_url": "/files/seal.png",
    }
    artifact = {
        "schema_version": 2,
        "index": 0,
        "region_id": "seal-0",
        "shape": "矩形",
        "shape_code": "rectangle",
        "inputs": [body_input],
        "reads": [],
        "errors": [],
    }

    class Reader:
        def read(self, path, *, provider):
            return [
                TextObservation("2310637", 0.99, 0.0, 0.35, 1.0, 0.5),
                TextObservation("三星电子维修中心", 0.99, 0.0, 0.05, 1.0, 0.2),
            ]

        def read_line(self, path, *, provider):
            return []

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    monkeypatch.setattr(
        pipeline,
        "prepare_inputs",
        lambda *args, **kwargs: (artifact, [(body_input, tmp_path / "seal.png")]),
    )

    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / "source.png",
        [],
        [SimpleNamespace()],
        tmp_path / "artifacts",
        "/files/artifacts",
        "paddle_v6",
        requirement="三星电子维修中心2310637",
    )

    assert texts == ["2310637", "三星电子维修中心", "三星电子维修中心2310637"]
    assert artifacts[0]["rectangular_text"] == "三星电子维修中心2310637"
    assert artifacts[0]["rectangular_texts"] == ["三星电子维修中心", "2310637"]
