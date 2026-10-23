"""Blue stamps must retain their ink through remote localization and local OCR."""
import shutil
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.colors import (
    _color_masks, _isolate_blue_ink, _ocr_color_mask, detect_seal_color,
)
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.crops import save_color_isolated_seal
from receipt_ocr.imaging.ring_border import trace_ellipse_border
from receipt_ocr.recognition.seal.preprocess import inputs
from receipt_ocr.recognition.seal.preprocess import horizontal_consensus
from receipt_ocr.recognition.seal.preprocess.oval_ring import prepare_oval_ring
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import rotate_stamp_image
from receipt_ocr.recognition.seal.preprocess.red_enhancement import enhance_oriented_seal
from receipt_ocr.recognition.seal.providers.regions import (
    qingtong_region_artifacts, qingtong_seal_regions,
)


def remote_check(*boxes):
    return {"dual_check": {"policy": "qingtong_any_channel", "candidates": [
        {"index": i, "xyxy": box, "cls": "ellipse"} for i, box in enumerate(boxes)
    ]}}


def test_remote_boxes_infer_each_stamp_colour_from_its_own_pixels(tmp_path):
    image = np.full((200, 500, 3), 255, np.uint8)
    image[20:180, 20:180] = (20, 20, 240)
    image[60:140, 310:390] = (240, 120, 20)
    # Black form text and a few red JPEG fringes cannot turn the blue crop red.
    image[25:45, 260:440] = (35, 35, 35)
    image[165:170, 260:270] = (235, 235, 245)
    source = tmp_path / "mixed.png"
    cv2.imwrite(str(source), image)
    check = remote_check([10, 10, 190, 190], [250, 10, 450, 190])
    regions = qingtong_seal_regions(source, check)
    assert [region.color for region in regions] == ["red", "blue"]
    assert regions[1].qingtong_cls == "ellipse"
    assert regions[1].x == .5 and regions[1].width == .4
    artifacts = qingtong_region_artifacts(source, check, tmp_path / "out", "/files")
    assert [item["color"] for item in artifacts] == ["red", "blue"]
    crop = cv2.imread(str(tmp_path / "out/seals/qingtong-1.png"))
    assert np.array_equal(crop, image[10:190, 250:450])


def test_pale_blue_is_detected_without_admitting_neutral_form_ink():
    image = np.array([[[250, 235, 235], [40, 40, 40], [240, 240, 240]]], np.uint8)
    assert detect_seal_color(image) == "blue"
    assert _color_masks(image)[1].tolist() == [[255, 0, 0]]
    assert _ocr_color_mask(image, "blue").tolist() == [[255, 0, 0]]
    assert detect_seal_color(image[:, 1:], default="red") == "red"


def test_remote_artifact_keeps_locally_confirmed_circle_over_remote_ellipse_hint(tmp_path):
    image = np.full((400, 400, 3), 255, np.uint8)
    cv2.circle(image, (200, 200), 150, (240, 70, 70), 6)
    source = tmp_path / "round.png"
    cv2.imwrite(str(source), image)
    artifacts = qingtong_region_artifacts(
        source, remote_check([0, 0, 400, 400]), tmp_path / "out", "/files")
    assert artifacts[0]["shape"] == "圆形"
    assert artifacts[0]["qingtong_shape_hint"] == "椭圆"


def test_blue_separation_preserves_dark_pale_and_cyan_strokes_and_removes_black():
    pixels = np.array([[[35, 5, 5], [250, 235, 235], [240, 120, 20],
                        [27, 24, 24], [75, 70, 70]]], np.uint8)
    actual = _isolate_blue_ink(pixels)
    assert actual[0, 0, 1] < 80
    assert actual[0, 1, 1] < 235
    assert actual[0, 2, 1] < 100
    assert np.all(actual[0, 3] == 255)
    assert actual[0, 4, 1] > 240
    assert np.all(actual[:, :, 0] == 255)
    neutral = np.array([[[n, n, n] for n in range(256)]], np.uint8)
    assert np.all(_isolate_blue_ink(neutral) == 255)


def test_blue_crossings_survive_tinted_horizontal_and_vertical_rules():
    image = np.full((180, 260, 3), 255, np.uint8)
    cv2.line(image, (5, 90), (255, 90), (47, 35, 35), 3)
    cv2.line(image, (130, 5), (130, 175), (47, 35, 35), 3)
    image[85:96, 55:60] = (30, 5, 5)
    image[40:45, 125:136] = (30, 5, 5)
    actual = _isolate_blue_ink(image)
    assert np.all(actual[90, 20] == 255)
    assert np.all(actual[20, 130] == 255)
    assert actual[90, 57, 1] < 80
    assert actual[42, 130, 1] < 80


@pytest.mark.parametrize("rotations", [[], [90, -7]])
def test_blue_source_chroma_enhancement_keeps_rotation_frame(tmp_path, rotations):
    source = tmp_path / "source.png"
    image = np.full((240, 1000, 3), 255, np.uint8)
    image[40:80, 100:160] = (40, 40, 40)
    image[40:80, 300:360] = (245, 220, 220)
    image[40:80, 500:560] = (75, 70, 70)
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, "blue", "客户章", 0)
    working = tmp_path / "color.png"
    save_color_isolated_seal(source, working, region)
    for i, angle in enumerate(rotations):
        target = tmp_path / f"rotated-{i}.png"
        rotate_stamp_image(working, target, angle)
        working = target
    destination = tmp_path / "enhanced.png"
    before = working.read_bytes()
    assert enhance_oriented_seal(source, region, working, destination,
                                 {"rotation_steps": rotations}) == destination
    actual = cv2.imread(str(destination))
    assert actual.shape == cv2.imread(str(working)).shape
    assert working.read_bytes() == before
    assert np.all(actual[:, :, 0] == 255)
    assert (actual[:, :, 1] < 230).sum() > 1000
    if not rotations:
        assert np.all(actual[60, 120] == 255)
        assert 180 < actual[60, 320, 1] < 220
        assert actual[60, 520, 1] > 230


def test_blue_oval_source_chroma_keeps_colour_and_exact_text_mask(tmp_path):
    image = np.full((800, 1000, 3), 255, np.uint8)
    cv2.ellipse(image, (500, 400), (440, 330), 0, 0, 360, (240, 100, 100), 5)
    image[200:220, 400:420] = (20, 20, 20)
    image[200:220, 500:520] = (250, 230, 230)
    image[450:480, 450:550] = (240, 120, 120)
    source, mask_path = tmp_path / "blue.png", tmp_path / "mask.png"
    cv2.imwrite(str(source), image)
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[450:480, 450:550] = 255
    cv2.imwrite(str(mask_path), mask)
    reader = SimpleNamespace(read_line=lambda *a, **k: [
        SimpleNamespace(text="蓝色印章有限公司", confidence=.99)])
    result = prepare_oval_ring(
        source, SealRegion(0, 0, 1, 1, "blue", "客户章", 0), source, mask_path,
        trace_ellipse_border(source, color="blue"), [], tmp_path / "native.png",
        reader=reader, provider="paddle_v6",
    )
    assert result is not None
    raw, covered = [cv2.imread(str(result[key])) for key in ("source", "input")]
    assert np.all(raw[:, :, 0] == 255)
    assert np.all(raw[210, 410] == 255)
    assert 200 < raw[210, 510, 1] < 255
    assert np.all(covered[mask > 0] == 255)
    assert np.array_equal(raw[mask == 0], covered[mask == 0])


def test_blue_horizontal_consensus_keeps_blue_ring_evidence(tmp_path, monkeypatch):
    image = np.full((800, 1000, 3), 255, np.uint8)
    cv2.ellipse(image, (500, 400), (440, 330), 0, 0, 360, (240, 100, 100), 5)
    cv2.putText(image, "COMPANY", (300, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (240, 100, 100), 4)
    image[450:480, 250:750] = (240, 100, 100)
    source, destination = tmp_path / "source.png", tmp_path / "consensus.png"
    cv2.imwrite(str(source), image)

    def detect(path, *args, **kwargs):
        if path.stem != "coarse-0":
            return []
        return [{"text": "收发货专用章", "points": [[250, 450], [750, 450], [750, 480], [250, 480]]}]

    monkeypatch.setattr(horizontal_consensus, "detect_orientation_boxes", detect)
    reader = SimpleNamespace(read_line=lambda *a, **k: [
        SimpleNamespace(text="收发货专用章", confidence=.99)])
    result = horizontal_consensus.prepare_horizontal_consensus(
        source, source, destination, reader=reader, provider="paddle_v6",
        model_variant="v6", ink_color="blue",
    )
    assert result is not None
    assert result["orientation"]["type_band_text"] == "收发货专用章"
    assert result["audit"]["ring_evidence_quality"]["accepted"]
    actual = cv2.imread(str(result["working"]))
    assert np.all(actual[:, :, 0] == 255)
    assert (actual[:, :, 1] < 180).sum() > 1000
    assert "soft-blue" in {trial["mode"] for trial in result["audit"]["candidates"]}


@pytest.mark.parametrize("mode", ["none", "combined"])
def test_remote_blue_ellipse_reaches_real_ring_input_with_visible_ink(tmp_path, monkeypatch, mode):
    image = np.full((800, 1000, 3), 255, np.uint8)
    cv2.ellipse(image, (500, 400), (430, 310), 0, 0, 360, (240, 50, 50), 7)
    cv2.putText(image, "COMPANY", (300, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (240, 50, 50), 4)
    image[450:480, 350:650] = (240, 50, 50)
    source = tmp_path / "blue.png"
    cv2.imwrite(str(source), image)
    region, = qingtong_seal_regions(source, remote_check([0, 0, 1000, 800]))
    monkeypatch.setattr(inputs, "classify_shape", lambda *a: "ellipse")
    monkeypatch.setattr(inputs, "prepare_oval_ring", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "prepare_multi_zone", lambda *a, **k: None)

    def orient(source, target, **kwargs):
        shutil.copyfile(source, target)
        return target, {"rotation_steps": [], "type_band_text": "收发货专用章",
                        "oriented_type_row_exact_box": [345, 445, 655, 485]}

    monkeypatch.setattr(inputs, "prepare_round_stamp_combined", orient)
    monkeypatch.setattr(inputs, "prepare_local_type_row", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "prepare_number_input", lambda *a, **k: {"status": "not_detected"})
    directory = tmp_path / "out"
    artifact, jobs = inputs.prepare_inputs(source, region, 0, directory, "/files", "paddle_v6", mode)
    assert artifact["color"] == "blue"
    assert artifact["errors"] == []
    assert ("blue_enhancement" in artifact) == (mode == "combined")
    assert "red_enhancement" not in artifact
    assert artifact["ring_border_detection"]["supported_fraction"] > .8
    assert [item["channel"] for item, _ in jobs] == (["ring"] if mode == "none" else ["center", "ring"])
    ring = cv2.imread(str(jobs[-1][1]), cv2.IMREAD_GRAYSCALE)
    assert (ring < 180).sum() > 500
    if mode == "combined":
        masked = cv2.imread(str(directory / "seal-0-ring-input.png"))
        assert np.all(masked[450:480, 350:650] == 255)
