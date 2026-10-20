import shutil

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess import inputs, rectangle_orientation
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry


@pytest.mark.parametrize("angle", [0, 8, -90, 180])
def test_rectangle_detects_once_and_maps_same_boxes(monkeypatch, tmp_path, angle):
    source = tmp_path / "source.png"
    image = np.full((180, 400, 3), 255, np.uint8)
    image[30:60, 10:380] = (20, 20, 220)
    image[100:140, 100:300] = (50, 50, 230)
    cv2.imwrite(str(source), image)
    boxes = [
        {"text": "郑州广利达电子技术有限公司", "confidence": .98,
         "points": [[10, 30], [380, 30], [380, 60], [10, 60]]},
        {"text": "业务受理", "confidence": .99,
         "points": [[100, 100], [300, 100], [300, 140], [100, 140]]},
    ]
    calls = []

    def detect(path, **kwargs):
        calls.append(path)
        assert len(calls) == 1, "Never localize a second time on the padded image"
        return boxes

    monkeypatch.setattr("receipt_ocr.recognition.seal.ocr.interface.detect_boxes", detect)
    monkeypatch.setattr(rectangle_orientation, "choose_rectangular_stamp_angle",
                        lambda rows: {"applied_rotation": angle})
    monkeypatch.setattr(inputs, "classify_shape", lambda *_: "rectangle")
    monkeypatch.setattr(inputs, "save_region_crop", lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, "save_color_isolated_seal", lambda s, d, r: shutil.copyfile(s, d))
    line_calls = []

    class Reader:
        def read(self, *args, **kwargs):
            pytest.fail("Body redetection would replace the first boxes")

        def read_line(self, path, **kwargs):
            line_calls.append(path)
            return [TextObservation("文字", .98, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    texts, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, "red", "客户章", 0)],
        tmp_path / "artifacts", "/files/test", "paddle_v6", orientation_mode="combined",
    )
    artifact = artifacts[0]
    assert not artifact["errors"]
    assert len(calls) == 1
    assert artifact["rectangular_layout"] == "single_detection"
    assert len(artifact["rectangular_row_urls"]) == 2
    assert len(line_calls) == (2 if angle else 0)
    if not angle:
        assert artifact["rectangular_text"] == "郑州广利达电子技术有限公司业务受理"
        assert all(r["image_url"].endswith("seal-0-color.png") for r in artifact["reads"])
        assert all(not i["ocr_input"] for i in artifact["inputs"] if i["name"].startswith("rectangle-row"))
        assert "郑州广利达电子技术有限公司业务受理" in texts
    else:
        assert artifact["rectangular_text"] == "文字文字"
        assert all("rectangular-row" in r["image_url"] for r in artifact["reads"])
    pad = artifact["orientation"]["padding"]
    for row in artifact["rectangular_fixed_rows"]:
        expected = np.array(boxes[row["detection_index"]]["points"], np.float32)
        if angle:
            matrix, _, _ = _rotation_geometry(400 + 2 * pad, 180 + 2 * pad, angle)
            expected = cv2.transform((expected + pad)[None], matrix)[0]
        assert np.allclose(row["points"], expected)
        # The saved crop contains every detected corner, including the first glyph.
        left, top, right, bottom = row["crop_box"]
        assert left <= expected[:, 0].min() < expected[:, 0].max() < right
        assert top <= expected[:, 1].min() < expected[:, 1].max() < bottom


def test_none_orientation_still_localizes_only_once(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    cv2.imwrite(str(source), np.full((100, 300, 3), 255, np.uint8))
    monkeypatch.setattr(inputs, "classify_shape", lambda *_: "rectangle")
    calls = []

    class Reader:
        def detect_boxes(self, path, **kwargs):
            calls.append(path)
            return [{"text": "郑州", "confidence": .98,
                     "points": [[10, 10], [200, 10], [200, 50], [10, 50]]}]

        def read(self, *args, **kwargs):
            pytest.fail("No second detection")

        read_line = read

    monkeypatch.setattr(inputs, "OcrReader", Reader)
    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    _, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, "red", "客户章", 0)],
        tmp_path / "artifacts", "", "paddle_v6", orientation_mode="none",
    )
    assert len(calls) == 1
    assert not artifacts[0]["errors"]
    assert artifacts[0]["rectangular_text"] == "郑州"


def test_empty_first_detection_does_not_fabricate_or_redetect(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    cv2.imwrite(str(source), np.full((100, 300, 3), 255, np.uint8))
    monkeypatch.setattr(inputs, "classify_shape", lambda *_: "rectangle")

    class Reader:
        def detect_boxes(self, path, **kwargs):
            return []

        def read(self, *args, **kwargs):
            pytest.fail("No second detection")

        read_line = read

    monkeypatch.setattr(inputs, "OcrReader", Reader)
    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    texts, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, "red", "客户章", 0)],
        tmp_path / "artifacts", "", "paddle_v6", orientation_mode="none",
    )
    assert texts == []
    assert not artifacts[0]["rectangular_fixed_rows"]
    assert not artifacts[0]["reads"]
