from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess.number import (
    _tight_number_box, merge_number_read, prepare_number_input, seal_number,
)


def detected(text="(1", confidence=.95, x=80, y=10, width=40, height=40, angle=0):
    return {"text": text, "confidence": confidence, "angle": angle,
            "points": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]}


@pytest.mark.parametrize("text,expected", [
    ("(1", "1"), ("（１２）", "12"), ("3)", "3"), (" 01 ", "01"),
    ("(I)", ""), ("1(2", ""), ("2026", ""), ("10月", ""), ("①", ""),
])
def test_numbers_require_actual_digits_and_preserve_leading_zeroes(text, expected):
    assert seal_number(text) == expected


def prepare(tmp_path, boxes):
    source, destination = tmp_path / "upright.png", tmp_path / "number.png"
    image = Image.new("RGB", (400, 400), "white")
    image.paste((255, 60, 60), (180, 210, 220, 250))
    image.save(source)

    def detect(path, *, provider):
        # The detector must see only the area under the actual type row.
        assert provider == "paddle_v6"
        with Image.open(path) as search:
            assert search.size == (200, 75)
            assert search.getpixel((90, 20)) == (255, 60, 60)
        return boxes

    result = prepare_number_input(source, destination, [100, 150, 300, 200],
                                  reader=SimpleNamespace(detect_boxes=detect), provider="paddle_v6")
    return result, destination


def test_number_crop_uses_final_upright_coordinates_and_retains_original_pixels(tmp_path):
    evidence, destination = prepare(tmp_path, [detected()])
    assert evidence["status"] == "detected"
    assert evidence["text"] == "(1"
    assert evidence["search_box"] == [100, 200, 300, 275]
    box = evidence["box"]
    assert box[0] <= 180 and box[1] <= 210 and box[2] >= 220 and box[3] >= 250
    with Image.open(destination) as crop:
        assert crop.getpixel((190 - box[0], 220 - box[1])) == (255, 60, 60)


@pytest.mark.parametrize("box", [
    detected("2026"), detected("10月"), detected("1", .4), detected("1", float("nan")),
    detected(x=0), detected(y=45), detected(angle=65), detected(height=8),
])
def test_dates_rim_text_distant_rows_and_unreliable_numbers_are_excluded(tmp_path, box):
    evidence, destination = prepare(tmp_path, [box])
    assert evidence["status"] == "not_detected"
    assert not destination.exists()


def test_multiple_number_candidates_remain_ambiguous(tmp_path):
    evidence, destination = prepare(tmp_path, [detected("(1)"), detected("(2)", x=115)])
    assert evidence["status"] == "ambiguous"
    assert not destination.exists()


def number_with_adjacent_stripes():
    image = Image.new("RGB", (500, 450), "white")
    draw = ImageDraw.Draw(image)
    # Three separate glyphs below the centre/type row. They do not touch the
    # distracting side stripes or the connected bottom artwork.
    for box in [(200, 246, 208, 300), (239, 242, 252, 302), (280, 248, 288, 302)]:
        draw.rectangle(box, fill=(255, 80, 80))
    for x in range(140, 191, 10):
        draw.rectangle((x, 240, x + 4, 349), fill=(255, 40, 40))
    for x in range(310, 361, 10):
        draw.rectangle((x, 240, x + 4, 349), fill=(255, 40, 40))
    draw.rectangle((140, 330, 364, 349), fill=(255, 40, 40))
    return image


@pytest.mark.parametrize("scale", [.5, 1, 2])
def test_tight_crop_follows_glyphs_at_different_scales_without_adjacent_stripes(scale):
    image = number_with_adjacent_stripes().crop((100, 230, 400, 350))
    image = image.resize((round(300 * scale), round(120 * scale)), Image.Resampling.NEAREST)
    box = _tight_number_box(image, 80 * scale)
    assert box is not None
    # Each visible glyph survives, but the connected side/bottom artwork is
    # outside the crop. Thresholding is used only for locating this rectangle.
    assert 91 * scale < box[0] <= 100 * scale
    assert 8 * scale <= box[1] <= 12 * scale
    assert 189 * scale <= box[2] < 210 * scale
    assert 73 * scale <= box[3] < 100 * scale


def test_detector_and_final_number_read_receive_same_tight_color_pixels(tmp_path):
    image = number_with_adjacent_stripes()
    source, destination = tmp_path / "stamp.png", tmp_path / "number.png"
    image.save(source)
    detector_crops = []

    def detect(path, *, provider):
        with Image.open(path) as crop:
            detector_crops.append(crop.copy())
            w, h = crop.size
            assert w < 110 and h < 80
            assert (255, 40, 40) not in crop.getdata()
            assert (255, 80, 80) in crop.getdata()
        return [detected("(2)", x=0, y=0, width=w, height=h)]

    evidence = prepare_number_input(source, destination, [100, 150, 400, 230],
                                    reader=SimpleNamespace(detect_boxes=detect), provider="paddle_v6")
    assert evidence["status"] == "detected"
    assert evidence["method"] == "tight_ink_components"
    assert evidence["text"] == "(2)"
    assert evidence["box"] == evidence["tight_box"]
    with Image.open(destination) as final:
        assert final.tobytes() == detector_crops[0].tobytes()
        assert final.tobytes() == image.crop(evidence["box"]).tobytes()
    with Image.open(source) as original:
        assert original.tobytes() == image.tobytes()


@pytest.mark.parametrize("layout", ["blank", "stripes_only", "off_center", "different_rows"])
def test_tight_geometry_does_not_manufacture_a_number_from_unrelated_ink(layout):
    image = Image.new("RGB", (300, 120), "white")
    draw = ImageDraw.Draw(image)
    if layout == "stripes_only":
        draw.rectangle((90, 5, 210, 119), fill="red")
    elif layout == "off_center":
        for x in (10, 25, 40):
            draw.rectangle((x, 10, x + 5, 70), fill="red")
    elif layout == "different_rows":
        draw.rectangle((100, 0, 110, 40), fill="red")
        draw.rectangle((150, 55, 160, 95), fill="red")
    assert _tight_number_box(image, 80) is None


def numbered_artifact(number="(1", detected_number="(1"):
    return {
        "schema_version": 2, "index": 0, "region_id": "seal-0", "shape_code": "round", "color": "red",
        "original_url": "/files/original.png", "color_isolated_oriented_url": "/files/clean.png",
        "unwrapped_text": "深圳测试有限公司", "round_type_band_text": "收货专用章",
        "reads": [
            {"channel": "center", "region_id": "seal-0", "provider": "paddle_v6",
             "text": "收货专用章", "confidence": .99},
            {"channel": "number", "region_id": "seal-0", "provider": "paddle_v6",
             "text": number, "confidence": .92},
        ],
        "stamp_number_detection": {"status": "detected", "text": detected_number, "confidence": .95},
    }


def test_same_stamp_number_matches_without_manufacturing_missing_parenthesis():
    artifact = numbered_artifact()
    raw_reads = deepcopy(artifact["reads"])
    merge_number_read(artifact)
    assert artifact["round_type_band_text"] == "收货专用章(1"
    assert artifact["round_type_row_text"] == "收货专用章"
    assert artifact["reads"] == raw_reads
    result = {"fields": {"签章要求": "深圳测试有限公司收货专用章（1）"},
              "processing_artifacts": {"seals": [artifact]}}
    candidate = seal_candidates(result)[0]
    assert candidate["recognized"] == "深圳测试有限公司收货专用章(1"
    assert candidate["text_status"] == "匹配"
    # Different numbers still fail even when the company and stamp type agree.
    result["fields"]["签章要求"] = "深圳测试有限公司收货专用章（2）"
    assert seal_candidates(result)[0]["text_status"] == "不匹配"


@pytest.mark.parametrize("change", [
    {"confidence": .4}, {"confidence": float("nan")}, {"text": "(2)"},
    {"text": "I"}, {"region_id": "seal-1"}, {"provider": "qingtong"},
])
def test_number_must_be_reliable_and_agree_with_detection_from_same_stamp_and_provider(change):
    artifact = numbered_artifact()
    artifact["reads"][1].update(change)
    merge_number_read(artifact)
    assert artifact["round_type_band_text"] == "收货专用章"
    assert artifact["stamp_number_status"] == "unverified"


def test_missing_number_is_not_filled_from_orientation_or_requirement():
    artifact = numbered_artifact()
    artifact["reads"] = artifact["reads"][:1]
    artifact["orientation"] = {"type_row_texts": ["收货专用章", "(1)"]}
    merge_number_read(artifact)
    assert artifact["round_type_band_text"] == "收货专用章"
    assert artifact["stamp_number_status"] == "unverified"


def test_number_already_on_type_row_is_not_duplicated():
    artifact = numbered_artifact()
    artifact["reads"][0]["text"] = artifact["round_type_band_text"] = "收货专用章(1)"
    merge_number_read(artifact)
    assert artifact["round_type_band_text"] == "收货专用章(1)"
    assert artifact["stamp_number_status"] == "already_in_type_row"


def test_pipeline_adds_separate_number_to_center_and_keeps_auditable_reads(monkeypatch, tmp_path):
    artifact = numbered_artifact()
    artifact.update(reads=[], inputs=[], errors=[])
    jobs = []
    for channel in ("center", "number", "ring"):
        item = {"id": f"seal-0:{channel}", "channel": channel, "image_url": f"/files/{channel}.png"}
        artifact["inputs"].append(item)
        jobs.append((item, tmp_path / channel))

    class Reader:
        def read_line(self, path, *, provider):
            text = {"center": "收货专用章", "number": "(1", "ring": "深圳测试有限公司"}[path.name]
            return [TextObservation(text, .95, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    monkeypatch.setattr(pipeline, "prepare_inputs", lambda *a: (artifact, jobs))
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / "source.png", [], [SimpleNamespace()], tmp_path, "/files", "paddle_v6",
    )
    assert "收货专用章(1" in texts
    assert artifacts[0]["round_type_band_text"] == "收货专用章(1"
    assert [(r["channel"], r["text"]) for r in artifacts[0]["reads"]] == [
        ("center", "收货专用章"), ("number", "(1"), ("ring", "深圳测试有限公司"),
    ]
