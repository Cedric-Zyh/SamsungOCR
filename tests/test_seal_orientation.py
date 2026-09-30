from receipt_ocr.recognition.seal.orientation import decide_orientation, choose_round_stamp_angle
import pytest
from receipt_ocr.recognition.seal import orientation as seal_orientation


@pytest.mark.parametrize('angle', [0, 90, 180, 270, None])
def test_doc_orientation_applies_predicted_correction_only(monkeypatch, tmp_path, angle):
    calls = []
    monkeypatch.setattr(seal_orientation, 'classify_doc_orientation',
                        lambda source: {'angle': angle, 'confidence': .99})
    def rotate(source, destination, correction):
        calls.append(correction)
        return destination
    monkeypatch.setattr(seal_orientation, 'rotate_stamp_image', rotate)
    destination = tmp_path / 'corrected.png'
    oriented, decision = seal_orientation.prepare_round_stamp_doc_ori('input.png', destination)
    assert decision['mode'] == 'doc_ori'
    assert decision['confidence'] == .99
    assert calls == ([angle] if angle else [])
    assert oriented == (destination if angle else None)


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
@pytest.mark.parametrize('confidence', [.4724, .7557, .8999, float('nan')])
def test_doc_orientation_keeps_uncertain_input_without_guessing_type_mask(
    monkeypatch, tmp_path, angle, confidence
):
    monkeypatch.setattr(seal_orientation, 'classify_doc_orientation',
                        lambda source: {'angle': angle, 'confidence': confidence})
    monkeypatch.setattr(seal_orientation, 'rotate_stamp_image',
                        lambda *args: pytest.fail('uncertain direction must not rotate'))
    oriented, decision = seal_orientation.prepare_round_stamp_doc_ori(
        'input.png', tmp_path / 'corrected.png')
    assert oriented is None
    assert decision['angle'] == angle
    assert decision['applied_rotation'] == 0
    assert '置信度不足' in decision['status']
    assert 'type_row_box' not in decision


@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_doc_orientation_does_not_mask_ring_without_detected_type_row(
    monkeypatch, tmp_path, angle
):
    from PIL import Image
    source = tmp_path / 'ring.png'
    Image.new('RGB', (200, 200), 'white').save(source)
    monkeypatch.setattr(seal_orientation, 'classify_doc_orientation',
                        lambda source: {'angle': angle, 'confidence': .99})
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.detect_boxes', lambda *a, **k: [])
    _, decision = seal_orientation.prepare_round_stamp_doc_ori(source, tmp_path / 'corrected.png')
    assert 'type_row_box' not in decision
    assert 'oriented_type_row_box' not in decision


def test_combined_keeps_original_when_no_angle_reads_a_stamp_type_row(monkeypatch, tmp_path):
    from PIL import Image
    source = tmp_path / 'ring.png'
    Image.new('RGB', (200, 200), 'white').save(source)
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.detect_boxes', lambda *a, **k: [])
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.read_line', lambda *a, **k: [])
    oriented, decision = seal_orientation.prepare_round_stamp_combined(
        source, tmp_path / 'corrected.png')
    assert oriented is None
    assert decision['applied_rotation'] == 0
    assert decision['coarse_rotation'] == 0
    assert '未找到章型文字' in decision['status']
    assert not (tmp_path / 'corrected.png').exists()
    # Every right angle is compared, not only the classifier's single guess.
    assert set(decision['four_way_candidates']) == {'0', '90', '180', '270'}


def test_combined_picks_the_angle_whose_type_row_reads(monkeypatch, tmp_path):
    from pathlib import Path
    from PIL import Image
    from receipt_ocr.domain.ocr import TextObservation

    source = tmp_path / "input.png"
    Image.new("RGB", (200, 200), "white").save(source)
    rotations = []

    def rotate(_source, destination, correction):
        rotations.append(float(correction))
        Image.new("RGB", (200, 200), "white").save(destination)
        return destination

    monkeypatch.setattr(seal_orientation, "rotate_stamp_image", rotate)
    # Only the 90-degree candidate exposes a stamp-type polygon and a readable
    # centre row, so it must win over the unrotated crop.
    monkeypatch.setattr(
        "receipt_ocr.recognition.seal.ocr.interface.detect_boxes",
        lambda path, **_kwargs: (
            [{
                "text": "专用章",
                "confidence": 0.91,
                "angle": 6.5,
                "points": [[40, 90], [160, 90], [160, 110], [40, 110]],
            }]
            if Path(path).name.startswith("coarse-90") else []
        ),
    )
    monkeypatch.setattr(
        "receipt_ocr.recognition.seal.ocr.interface.read_line",
        lambda path, **_kwargs: (
            [TextObservation("售后服务专用章", 0.93, 0, 0, 1, 1)]
            if Path(path).name.startswith("coarse-90") else []
        ),
    )

    oriented, decision = seal_orientation.prepare_round_stamp_combined(
        source, tmp_path / "corrected.png"
    )

    assert oriented == tmp_path / "corrected.png"
    assert oriented.is_file()
    assert decision["mode"] == "combined"
    assert decision["coarse_rotation"] == 90.0
    assert decision["applied_rotation"] == 90.0
    assert decision["fine_rotation"] == 6.5
    assert decision["type_band_text"] == "售后服务专用章"
    assert decision["anchor_text"] == "专用章"
    assert "oriented_type_row_box" in decision
    assert "type_row_box" in decision
    # The fine pass runs per candidate before the comparison, so the coarse
    # quarter-turn of each angle is measured first.
    assert rotations == [90.0, 6.5, 180.0, 270.0]


def test_combined_never_uses_document_orientation_classifier(monkeypatch, tmp_path):
    from PIL import Image
    source = tmp_path / 'ring.png'
    Image.new('RGB', (200, 200), 'white').save(source)
    monkeypatch.setattr(
        seal_orientation, 'classify_doc_orientation',
        lambda *_args: pytest.fail('combined must not call doc_ori'),
    )
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.detect_boxes', lambda *a, **k: [])
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.read_line', lambda *a, **k: [])
    seal_orientation.prepare_round_stamp_combined(source, tmp_path / 'corrected.png')


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
    from receipt_ocr.domain.ocr import TextObservation

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
        "receipt_ocr.recognition.seal.ocr.interface.read_line",
        lambda *args, **kwargs: [TextObservation("供应链科技有限公司", .99, 0, 0, 1, 1)],
    )

    oriented, decision = seal_orientation.prepare_ellipse_stamp(
        source, tmp_path / "oriented.png", model_variant="v6"
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


def test_round_stamp_uses_high_confidence_partial_type_row_as_fallback_anchor():
    result = choose_round_stamp_angle([
        {
            "text": "手机售后专",
            "confidence": 0.99,
            "angle": 7.0,
            "points": [[10, 20], [60, 20], [60, 45], [10, 45]],
        }
    ])
    assert result["status"] == "找到横向文字候选（文本不完整）"
    assert result["anchor_text"] == "手机售后专"
    assert result["partial_anchor"] is True
    assert result["applied_rotation"] == 7.0


def test_ring_input_uses_oriented_image_and_its_detected_type_box():
    from pathlib import Path
    from receipt_ocr.recognition.seal.contracts import RegionEvidence
    from receipt_ocr.recognition.seal.preprocess.regions import _ring_input

    raw = Path("raw.png")
    oriented = Path("oriented.png")
    evidence = RegionEvidence(
        color_isolated=raw,
        color_isolated_oriented=oriented,
        orientation={
            "type_row_box": [10, 20, 90, 40],
            "oriented_type_row_box": [20, 30, 100, 50],
        },
    )
    assert _ring_input(evidence) == (oriented, [20, 30, 100, 50])
