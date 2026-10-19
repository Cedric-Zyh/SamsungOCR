from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.crops import seal_source_reference
from receipt_ocr.recognition.seal.preprocess import number_recovery as recovery
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import rotate_stamp_image


def stamp():
    clean = Image.new('RGB', (300, 300), 'white')
    draw = ImageDraw.Draw(clean)
    draw.arc((120, 160, 140, 190), 90, 270, fill=(255, 90, 90), width=3)
    draw.line((146, 162, 146, 190), fill=(255, 90, 90), width=3)
    draw.line((141, 190, 151, 190), fill=(255, 90, 90), width=3)
    reference = clean.copy()
    draw = ImageDraw.Draw(reference)
    draw.arc((155, 160, 175, 190), 270, 90, fill=(170, 170, 170), width=3)
    # Nearby form ink must never be copied back with a rectangular patch.
    draw.line((80, 205, 220, 205), fill=(15, 15, 15), width=2)
    detection = {'status': 'detected', 'text': '(1', 'confidence': .92,
                 'box': [118, 158, 154, 194], 'search_box': [50, 150, 250, 225]}
    return clean, reference, detection


@pytest.mark.parametrize('obstruction', ['absent', 'horizontal_rule', 'vertical_rule', 'black_core'])
def test_gray_recovery_rejects_missing_pixels_and_form_lines(obstruction):
    clean, reference, detection = stamp()
    if obstruction == 'absent':
        reference = clean
    else:
        draw = ImageDraw.Draw(reference)
        if obstruction == 'horizontal_rule':
            draw.line((90, 175, 210, 175), fill=(170, 170, 170), width=2)
        elif obstruction == 'vertical_rule':
            draw.line((173, 145, 173, 240), fill=(170, 170, 170), width=2)
        else:
            draw.arc((155, 160, 175, 190), 270, 90, fill=(20, 20, 20), width=3)
    assert recovery._adjacent_component(np.asarray(reference), detection, 'right') is None


@pytest.mark.parametrize('side', ['right', 'left'])
def test_recovery_selects_actual_adjacent_component_on_either_side(side):
    clean, reference, detection = stamp()
    pixels = np.asarray(reference)
    if side == 'left':
        pixels = np.fliplr(pixels).copy()
        l, t, r, b = detection['box']
        detection['box'] = [300-r, t, 300-l, b]
    mask, box = recovery._adjacent_component(pixels, detection, side)
    assert mask.sum() > 30
    assert not mask[205].any()
    assert np.all(pixels[mask] == 170)


@pytest.mark.parametrize('detector_text,line_text,accepted', [
    ('(1)', '(1)', True), ('(2)', '(2)', False), ('(1)', '(2)', False), ('(1', '(1', False),
])
def test_number_and_full_stamp_share_verified_source_pixels_only(
        tmp_path, monkeypatch, detector_text, line_text, accepted):
    clean, reference, detection = stamp()
    original_detection = deepcopy(detection)
    working, number = tmp_path/'clean.png', tmp_path/'number.png'
    destination, audit = tmp_path/'restored.png', tmp_path/'original-number.png'
    clean.save(working)
    clean.crop(detection['box']).save(number)
    before_number = number.read_bytes()
    monkeypatch.setattr(recovery, '_aligned_reference', lambda *a: np.asarray(reference)[:, :, ::-1].copy())

    class Reader:
        def detect_boxes(self, path, *, provider):
            with Image.open(path) as image:
                w, h = image.size
                assert w < 80 and h < 60
                assert image.getpixel((w-5, h//2))[0] == 255
            return [{'text': detector_text, 'confidence': .98, 'angle': 0,
                     'points': [[0, 0], [w, 0], [w, h], [0, h]]}]

        def read_line(self, path, *, provider):
            return [TextObservation(line_text, .98, 0, 0, 1, 1)]

    updated, evidence = recovery.recover_number_punctuation(
        tmp_path/'source.png', SimpleNamespace(color='red'), working, destination, number, audit,
        [50, 100, 250, 150], {'rotation_steps': []}, detection, reader=Reader(), provider='paddle_v6',
    )
    assert detection == original_detection
    assert np.array_equal(np.asarray(Image.open(working)), np.asarray(clean))
    if not accepted:
        assert updated == detection and evidence['status'] == 'not_recovered'
        assert number.read_bytes() == before_number
        assert not destination.exists() and not audit.exists()
        return
    assert updated['text'] == '(1)' and evidence['status'] == 'restored'
    final = np.asarray(Image.open(destination))
    mask, _ = recovery._adjacent_component(np.asarray(reference), detection, 'right')
    assert np.array_equal(final[~mask], np.asarray(clean)[~mask])
    assert np.any(final[mask] != np.asarray(clean)[mask])
    assert np.array_equal(np.asarray(Image.open(number)), np.asarray(Image.open(destination).crop(updated['box'])))
    assert np.array_equal(np.asarray(Image.open(audit)), np.asarray(reference.crop(updated['box'])))


@pytest.mark.parametrize('text', ['(1)', '1', '', '2026', '(I'])
def test_no_parenthesis_is_invented_without_an_incomplete_observed_number(text):
    assert recovery._missing_side(text) is None


def test_original_reference_replays_exact_resizing_and_each_rotation(tmp_path):
    source = tmp_path/'source.png'
    pixels = np.random.default_rng(8).integers(0, 256, (43, 67, 3), dtype=np.uint8)
    cv2.imwrite(str(source), pixels)
    region = SealRegion(.1, .2, .7, .6, 'red', '客户章', 0)
    base, coarse, fine = [tmp_path/f'{name}.png' for name in ('base', 'coarse', 'fine')]
    cv2.imwrite(str(base), seal_source_reference(source, region))
    rotate_stamp_image(base, coarse, 270)
    rotate_stamp_image(coarse, fine, 7.123456)
    replayed = recovery._aligned_reference(source, region, [270, 7.123456])
    assert np.array_equal(replayed, cv2.imread(str(fine)))
