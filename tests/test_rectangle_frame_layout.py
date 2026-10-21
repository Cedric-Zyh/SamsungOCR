import shutil

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess import inputs, rectangle_layout
from receipt_ocr.recognition.seal.preprocess.rectangle_frame import measure_frame, row_spans
from receipt_ocr.recognition.seal.preprocess.rectangle_columns import column_groups, reading_order


def box(text, rect):
    x, y, right, bottom = rect
    return {"text": text, "confidence": .98,
            "points": [[x, y], [right, y], [right, bottom], [x, bottom]]}


def test_staggered_columns_preserve_left_then_right_stack():
    # Detector order puts the small station label before the tall left name.
    boxes = [box("站代码", [550, 130, 750, 160]), box("标题", [50, 10, 850, 80]),
             box("上海信威：", [50, 135, 450, 220]), box("6237106", [550, 185, 800, 220]),
             box("电话", [50, 280, 850, 340])]
    groups = column_groups(boxes)
    assert groups == [[2, 0, 3]]
    assert reading_order(boxes, groups) == [1, 2, 0, 3, 4]
    assert not column_groups([boxes[1], boxes[4]])


def test_frame_rejects_missing_rims_and_preserves_full_width():
    image = np.full((200, 400, 3), 255, np.uint8)
    image[40:65, 15:130] = (30, 30, 230)
    image[120:160, 290:385] = (220, 220, 250)
    with pytest.raises(ValueError):
        measure_frame(image)
    cv2.rectangle(image, (5, 5), (395, 195), (0, 0, 255), 4)
    oriented, (left, top, right, bottom), _ = measure_frame(image)
    interior = oriented[top:bottom, left:right]
    spans = row_spans(interior)
    assert len(spans) == 2
    assert left < 15 and right > 385
    # Faint right-hand strokes remain available to recognition, not thresholded away.
    assert np.any(np.all(interior[spans[1][0]:spans[1][1]] == (220, 220, 250), axis=2))


@pytest.mark.parametrize("columns", [False, True])
def test_pipeline_consumes_exact_prepared_inputs_without_redetection(monkeypatch, tmp_path, columns):
    source = tmp_path / "source.png"
    image = np.full((300, 600, 3), 255, np.uint8)
    image[30:90, 30:250] = (0, 0, 255)
    image[170:240, 300:550] = (0, 0, 255)
    cv2.imwrite(str(source), image)
    monkeypatch.setattr(rectangle_layout, "measure_frame", lambda im: (im, [10, 10, 590, 290], 0))
    monkeypatch.setattr(inputs, "classify_shape", lambda *_: "rectangle")
    monkeypatch.setattr(inputs, "save_region_crop", lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, "save_color_isolated_seal", lambda s, d, r: shutil.copyfile(s, d))
    calls, line_calls = [], []
    boxes = [box("站代码", [550, 130, 750, 160]), box("三星电子服务中心", [50, 40, 850, 80]),
             box("上海信威：", [50, 135, 450, 220]), box("6237106", [550, 185, 800, 220])]

    class Reader:
        def detect_boxes(self, path, **kwargs):
            calls.append(path)
            assert cv2.imread(str(path)).shape[1] == 1024
            return boxes if columns else [boxes[1]]

        def read(self, *args, **kwargs):
            pytest.fail("No body redetection or merged-row fallback")

        def read_line(self, path, **kwargs):
            line_calls.append(path)
            return [TextObservation(["三星电子维修中心", "5972036"][len(line_calls) - 1], .98, 0, 0, 1, 1)]

    monkeypatch.setattr(inputs, "OcrReader", Reader)
    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    texts, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, "red", "客户章", 0)],
        tmp_path / "artifacts", "/files/test", "paddle_v6", orientation_mode="combined",
    )
    artifact = artifacts[0]
    assert not artifact["errors"]
    assert len(calls) == 1
    assert artifact["rectangular_layout"] == ("frame_columns" if columns else "frame_rows")
    assert len(line_calls) == (0 if columns else 2)
    assert artifact["rectangular_text"] == (
        "三星电子服务中心上海信威：站代码6237106" if columns else "三星电子维修中心5972036"
    )
    assert artifact["rectangular_text"] in texts
    if columns:
        assert all(r["image_url"].endswith("rectangle-layout.png") for r in artifact["reads"])
        assert all(not i["ocr_input"] for i in artifact["inputs"] if "rectangular-row" in i["name"])
    else:
        assert all("rectangular-row" in r["image_url"] for r in artifact["reads"])
