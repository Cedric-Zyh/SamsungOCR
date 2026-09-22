from receipt_ocr.seal_orientation import decide_orientation, choose_round_stamp_angle
import pytest
from receipt_ocr import seal_orientation


@pytest.mark.parametrize('angle', [0, 90, 180, 270, None])
def test_doc_orientation_applies_predicted_correction_only(monkeypatch, tmp_path, angle):
    calls = []
    monkeypatch.setattr(seal_orientation, 'classify_doc_orientation',
                        lambda source: {'angle': angle, 'confidence': .4724})
    def rotate(source, destination, correction):
        calls.append(correction)
        return destination
    monkeypatch.setattr(seal_orientation, 'rotate_stamp_image', rotate)
    destination = tmp_path / 'corrected.png'
    oriented, decision = seal_orientation.prepare_round_stamp_doc_ori('input.png', destination)
    assert decision['mode'] == 'doc_ori'
    assert decision['confidence'] == .4724
    assert calls == ([angle] if angle else [])
    assert oriented == (destination if angle else None)


def test_orientation_none_bypasses_rectangle_direction_model(monkeypatch, tmp_path):
    from receipt_ocr import seal_crops
    monkeypatch.setattr(seal_orientation, 'prepare_rectangles',
                        lambda *args: pytest.fail('orientation model must not run'))
    calls = []
    def recognize(*args):
        calls.append(args)
        return [], []
    monkeypatch.setattr(seal_crops, '_recognize_oriented_seals', recognize)
    seal_crops._recognize_local_seals(tmp_path / 'input.png', [], [], None, '', 'paddle',
                                    orientation_mode='none')
    assert calls[0][9] == 'none'


def test_rectangle_orientation_requires_unanimous_high_confidence_180():
    result = decide_orientation([
        {"label_names": ["180_degree"], "scores": [0.99]},
        {"label_names": ["180_degree"], "scores": [0.96]},
    ])
    assert result["angle"] == 180
    assert result["status"] == "方向已确认"
    assert result["confidence"] == 0.96


def test_rectangle_orientation_does_not_rotate_on_conflict_or_low_confidence():
    conflict = decide_orientation([
        {"label_names": ["180_degree"], "scores": [0.99]},
        {"label_names": ["0_degree"], "scores": [0.99]},
    ])
    weak = decide_orientation([
        {"label_names": ["180_degree"], "scores": [0.69]},
    ])
    assert conflict["angle"] is None
    assert weak["angle"] is None


def test_round_stamp_uses_partial_stamp_type_polygon_as_angle_anchor():
    result = choose_round_stamp_angle([
        {"text": "货专用章", "confidence": 0.99, "angle": 59.8},
        {"text": "供应商", "confidence": 0.99, "angle": 12.0},
    ])
    assert result["anchor_text"] == "货专用章"
    assert result["angle"] == 59.8
    assert result["applied_rotation"] == 59.8


def test_round_stamp_uses_short_receiving_stamp_type_as_angle_anchor():
    result = choose_round_stamp_angle([
        {
            "text": "收货章",
            "confidence": 0.98,
            "angle": 4.0,
            "points": [[0, 0], [100, 0], [100, 20], [0, 20]],
        }
    ])

    assert result["status"] == "找到印章类型方向锚点"
    assert result["anchor_text"] == "收货章"
    assert result["applied_rotation"] == 4.0


def test_ellipse_orientation_does_not_flip_on_ring_company_text(monkeypatch, tmp_path):
    from PIL import Image
    from receipt_ocr.ocr_types import TextObservation

    source = tmp_path / "ellipse.png"
    Image.new("RGB", (200, 200), "white").save(source)
    monkeypatch.setattr(
        seal_orientation,
        "prepare_round_stamp",
        lambda *args, **kwargs: (
            None,
            {"mode": "polygon", "anchor_text": "", "applied_rotation": 0.0},
        ),
    )
    monkeypatch.setattr(
        "receipt_ocr.paddle_ocr.recognize_line",
        lambda *args, **kwargs: [TextObservation("供应链科技有限公司", .99, 0, 0, 1, 1)],
    )

    oriented, decision = seal_orientation.prepare_ellipse_stamp(
        source, tmp_path / "oriented.png", model_variant="mobile"
    )

    assert oriented is None
    assert decision["applied_rotation"] == 0.0
    assert "未确认" in decision["status"]


def test_round_stamp_does_not_infer_direction_from_unrelated_text():
    result = choose_round_stamp_angle([
        {"text": "供应商", "confidence": 0.99, "angle": 59.8},
        {"text": "用章", "confidence": 0.69, "angle": 59.8},
    ])
    assert result["angle"] is None
    assert result["applied_rotation"] == 0.0
