from receipt_ocr.recognition.seal.preprocess import doc_orientation, combined_orientation
from receipt_ocr.recognition.seal.orientation import decide_orientation, choose_round_stamp_angle
import pytest
from receipt_ocr.recognition.seal import orientation as seal_orientation


@pytest.mark.parametrize('angle', [0, 90, 180, 270, None])
def test_doc_orientation_applies_predicted_correction_only(monkeypatch, tmp_path, angle):
    calls = []
    monkeypatch.setattr(doc_orientation, 'classify_doc_orientation',
                        lambda source: {'angle': angle, 'confidence': .99})
    def rotate(source, destination, correction):
        calls.append(correction)
        return destination
    monkeypatch.setattr(doc_orientation, 'rotate_stamp_image', rotate)
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
    monkeypatch.setattr(doc_orientation, 'classify_doc_orientation',
                        lambda source: {'angle': angle, 'confidence': confidence})
    monkeypatch.setattr(doc_orientation, 'rotate_stamp_image',
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
    monkeypatch.setattr(doc_orientation, 'classify_doc_orientation',
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
    assert set(decision['four_way_candidates']) == {'0', '90', '180', '270', '45', '135', '225', '315'}


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

    monkeypatch.setattr(combined_orientation, "rotate_stamp_image", rotate)
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
        doc_orientation, 'classify_doc_orientation',
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


def test_four_way_equivalent_text_prefers_small_fine_correction():
    def candidate(angle, fine, confidence, text='收货专用章'):
        return dict(angle=angle, fine_rotation=fine, band_confidence=confidence,
                    band_text=text, rank=((1, 5, 3), 1, confidence), error='')
    original = candidate(0, -11.723, .965)
    sideways = candidate(270, 18.254, .9865)
    select = combined_orientation._select_four_way_candidate
    assert select([original, sideways]) is original
    stronger = candidate(270, 18.254, .999)
    assert select([original, stronger]) is stronger
    different = candidate(270, 18.254, .9865, '收货验收专用章')
    assert select([original, different]) is different


def test_four_way_rejects_good_anchor_when_final_row_is_unreadable():
    original = dict(angle=0, fine_rotation=0., band_text='收诉专用章',
                    band_confidence=.6178, anchor_text='', error='')
    flipped = dict(angle=270, fine_rotation=-6., band_text='量',
                   band_confidence=.99, anchor_text='收界专用章', error='')
    selected = combined_orientation._select_four_way_candidate([original, flipped])
    assert selected is original
    assert original['post_rotation_verified']
    assert not flipped['post_rotation_verified']
    assert flipped['rejection_reason'] == '转正后未读到可靠章型行'


@pytest.mark.parametrize('fine', [-86.179, 78.254])
def test_four_way_rejects_quarter_turn_as_fine_correction(fine):
    original = dict(angle=0, fine_rotation=3.1, band_text='收货专用章',
                    band_confidence=.81, error='')
    sideways = dict(angle=270, fine_rotation=fine, band_text='收货专用章',
                    band_confidence=.9999, error='')
    assert combined_orientation._select_four_way_candidate([original, sideways]) is original
    assert sideways['rejection_reason'] == '微调角度超过 45°'


@pytest.mark.parametrize('confidence', [.54, float('nan')])
def test_combined_does_not_rotate_on_unverified_final_row(monkeypatch, tmp_path, confidence):
    from pathlib import Path
    from PIL import Image
    from receipt_ocr.domain.ocr import TextObservation
    source = tmp_path / 'stamp.png'
    Image.new('RGB', (200, 200), 'white').save(source)
    monkeypatch.setattr(
        'receipt_ocr.recognition.seal.ocr.interface.detect_boxes',
        lambda path, **kwargs: ([dict(text='收货专用章', confidence=.99, angle=0,
            points=[[20, 90], [180, 90], [180, 110], [20, 110]])]
            if Path(path).name.startswith('coarse-90') else []),
    )
    monkeypatch.setattr(
        'receipt_ocr.recognition.seal.ocr.interface.read_line',
        lambda *args, **kwargs: [TextObservation('收货专用章', confidence, 0, 0, 1, 1)],
    )
    target = tmp_path / 'oriented.png'
    oriented, decision = seal_orientation.prepare_round_stamp_combined(source, target)
    assert oriented is None
    assert not target.exists()
    assert decision['applied_rotation'] == 0
    assert not any(c['post_rotation_verified'] for c in decision['four_way_candidates'].values())


def test_no_fixed_center_crop_without_observed_horizontal_text(tmp_path, monkeypatch):
    from PIL import Image
    source = tmp_path / 'stamp.png'
    Image.new('RGB', (400, 400), 'white').save(source)
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.read_line',
                        lambda *a, **k: pytest.fail('no observed row, no OCR crop'))
    assert combined_orientation._read_type_band_text(
        source, tmp_path / 'row.png', model_variant='v6') == ('', 0.0)
    assert not (tmp_path / 'row.png').exists()


def test_diagonal_split_row_is_verified_and_mask_relocalized(tmp_path, monkeypatch):
    from pathlib import Path
    from PIL import Image
    from receipt_ocr.domain.ocr import TextObservation
    source = tmp_path / 'stamp.png'
    Image.new('RGB', (1000, 998), 'white').save(source)
    def detect(path, **kwargs):
        name = Path(path).name
        if name == 'coarse-315.png':
            return [
                {'text': '业务', 'confidence': .99, 'angle': 27.2949,
                 'points': [[384, 793], [696, 954], [588, 1163], [276, 1002]]},
                {'text': '用章', 'confidence': .99, 'angle': 20.4563,
                 'points': [[741, 878], [993, 972], [932, 1136], [680, 1043]]},
            ]
        if 'coarse-315-group-' in name:
            return [{'text': '业务专用章', 'confidence': .999, 'angle': 2.34,
                     'points': [[120, 65], [731, 89], [720, 350], [109, 325]]}]
        return []
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.detect_boxes', detect)
    monkeypatch.setattr('receipt_ocr.recognition.seal.ocr.interface.read_line', lambda path, **kw: [
        TextObservation('业务专用章' if 'group-' in Path(path).name else '专用章', .999, 0, 0, 1, 1)
    ])
    _, result = seal_orientation.prepare_round_stamp_combined(source, tmp_path / 'upright.png')
    assert result['coarse_rotation'] == 315
    assert result['fine_rotation'] == pytest.approx(4.770, abs=.001)
    assert result['type_band_text'] == '业务专用章'
    assert result['type_row_grouped'] and result['local_mask_verified']
    assert len(result['oriented_type_row_polygons']) == 1
    # The read crop may include whitespace. It must not become the fill box.
    assert result['oriented_type_row_read_box'] != result['oriented_type_row_exact_box']
