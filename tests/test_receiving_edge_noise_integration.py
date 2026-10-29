"""A border speck must not suppress the complete, consistent seal reading."""

from copy import deepcopy
from pathlib import Path

from PIL import Image

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal import receiving
from receipt_ocr.recognition.seal.postprocess.providers import combine_seal_provider_checks


TITLE = "上海金山新日仓021DCO"
PURPOSE = "收货专用章①"
REQUIREMENT = "上海金山新日仓021DC0" + PURPOSE


def test_border_speck_is_audited_without_overruling_agreeing_whole_rows(tmp_path, monkeypatch):
    texts = [TITLE, PURPOSE, "日期：收货人："]
    boxes = [
        {"text": "II", "confidence": .893624, "points": [[131, 31], [140, 31], [140, 42], [131, 42]]},
        {"text": TITLE, "confidence": .987304, "points": [[94, 62], [939, 62], [939, 150], [94, 150]]},
        {"text": PURPOSE, "confidence": .990407, "points": [[141, 183], [872, 181], [872, 318], [141, 320]]},
        {"text": "日期：", "confidence": .989472, "points": [[103, 372], [299, 372], [299, 465], [103, 465]]},
        {"text": "收货人：", "confidence": .959014, "points": [[497, 372], [757, 375], [756, 463], [496, 460]]},
    ]
    artifact = {
        "index": 0, "region_id": "seal-0", "color": "red", "shape": "矩形", "shape_code": "rectangle",
        "original_url": "/files/test/seal-0-original.png", "rectangular_layout": "frame_rows",
        "rectangular_frame": {"crop_box": [32, 40, 977, 476], "canonical_width": 960, "padding": 32,
                              "detected_boxes": deepcopy(boxes)},
        "rectangular_fixed_rows": [{"crop_box": [32, top, 977, bottom], "coordinate_space": "oriented"}
                                   for top, bottom in [(40, 177), (187, 332), (342, 476)]],
        "rectangular_row_urls": [f"/files/test/seal-0-rectangular-row-{i}.png" for i in range(3)],
        "inputs": [{"name": "rectangle-layout", "image_url": "/files/test/seal-0-rectangle-layout.png"}],
        "reads": [{"channel": "body", "rectangle_row_index": i, "text": text, "confidence": .99}
                  for i, text in enumerate(texts)], "errors": [],
    }
    for filename in ["seal-0-original.png", "seal-0-rectangle-layout.png",
                     *(f"seal-0-rectangular-row-{i}.png" for i in range(3))]:
        Image.new("RGB", (1024, 510), "white").save(tmp_path / filename)

    seen = []

    class Reader:
        def read_line(self, path, **options):
            name = Path(path).stem
            seen.append(name)
            index = int(name.rsplit("-", 1)[1])
            text = texts[index] if "primary-row" in name else boxes[index]["text"]
            return [TextObservation(text, .99, 0, 0, 1, 1)]

    monkeypatch.setattr(receiving, "prepare_receiving_grid", lambda *args: {"accepted": False})
    identity = receiving.recognize_receiving_form(artifact, tmp_path, "/files/test", "paddle_v6", Reader(),
                                                 orientation_mode="combined", requirement=REQUIREMENT)

    assert identity == [TITLE + PURPOSE]
    assert artifact["receiving_form"]["status"] == "adopted"
    assert artifact["receiving_form"]["primary"]["conflicts"] == []
    assert artifact["rectangular_frame"]["detected_boxes"] == boxes
    assert "seal-0-receiving-detection-0" not in seen
    assert [item["detection_index"] for item in artifact["receiving_detection_exclusions"]] == [0]
    local = compare_seal_text_strict(REQUIREMENT, identity, mismatch_over_partial=True)
    local.update(backend="本地 PaddleOCR", source="本地 PaddleOCR")
    remote = compare_seal_text_strict(REQUIREMENT, ["上海朱泾DC021DC0收货专用章1"])
    remote.update(backend="清瞳", source="清瞳", dual_check={"policy": "qingtong_any_channel"})
    selected = combine_seal_provider_checks([local, remote])
    assert selected["recognized"] == TITLE + PURPOSE
    assert selected["source"] == "本地 PaddleOCR"
    assert selected["status"] == "匹配" and selected["reliable"] is True
