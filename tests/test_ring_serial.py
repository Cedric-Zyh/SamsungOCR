import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging import unwrap
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess import ring_serial


def strip():
    image = np.full((200, 2400, 3), 255, np.uint8)
    cv2.putText(image, 'COMPANY', (700, 180), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 6)
    digits = np.full((55, 420, 3), 255, np.uint8)
    cv2.putText(digits, '5001038010311', (5, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 2)
    image[:55, 180:600] = cv2.rotate(digits, cv2.ROTATE_180)
    return image


@pytest.mark.parametrize('phase', [0, 300, 600, 1200, 2000])
@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_complete_digits_survive_circular_seam_and_scale(phase, scale):
    original = strip()
    scaled = cv2.resize(original, None, fx=scale, fy=scale)
    image = np.roll(scaled, -round(phase * scale), axis=1)
    before = image.copy()
    found = ring_serial.locate_ring_serial(image)
    assert found is not None
    assert found['glyph_count'] == 13
    left, top, right, bottom = found['crop_box']
    cropped = np.roll(image, found['shift'], axis=1)[top:bottom, left:right]
    reference = scaled[
        :round(55 * scale), round(180 * scale):round(600 * scale)]
    assert np.count_nonzero(cropped[:, :, 0] < 180) == np.count_nonzero(reference[:, :, 0] < 180)
    assert np.array_equal(image, before)


@pytest.mark.parametrize('case', ['company_only', 'detached_chinese_strokes'])
def test_company_ink_never_becomes_a_serial(case):
    image = strip()
    if case == 'company_only':
        image[:, 180:600] = 255
    else:
        cv2.rectangle(image, (190, 100), (590, 155), (0, 0, 0), 5)
    assert ring_serial.locate_ring_serial(image) is None


@pytest.mark.parametrize('phase', [0, 300, 1200])
@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_short_glyphs_are_kept_with_their_taller_neighbours(phase, scale):
    image = np.full((248, 2600, 3), 255, np.uint8)
    cv2.putText(image, 'COMPANY', (1000, 220), cv2.FONT_HERSHEY_SIMPLEX, 6, (0, 0, 0), 6)
    for index, digit in enumerate('3600000002665'):
        tile = np.full((70, 60, 3), 255, np.uint8)
        cv2.putText(tile, digit, (4, 54), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
        ys, xs = np.where(tile[:, :, 0] < 180)
        glyph = tile[ys.min():ys.max()+1, xs.min():xs.max()+1]
        height = [32, 34, 35, 38][index % 4]
        glyph = cv2.resize(glyph, (28, height))
        left = 180 + 50 * index
        image[72-height:72, left:left+28] = glyph
    scaled = cv2.resize(image, None, fx=scale, fy=scale)
    found = ring_serial.locate_ring_serial(np.roll(scaled, -round(phase * scale), axis=1))
    assert found is not None
    assert found['glyph_count'] == 13
    assert found['min_height'] < scaled.shape[0] * .14
    left, top, right, bottom = found['crop_box']
    unrolled = np.roll(scaled, found['shift'] - round(phase * scale), axis=1)
    crop = unrolled[top:bottom, left:right]
    reference = scaled[:round(80 * scale), round(180 * scale):round(808 * scale)]
    assert np.count_nonzero(crop[:, :, 0] < 180) == np.count_nonzero(reference[:, :, 0] < 180)


def prepare(monkeypatch, tmp_path):
    def save(source, destination, *args, **kwargs):
        assert kwargs['preserve_full_ring'] is True
        cv2.imwrite(str(destination), np.roll(strip(), -300, axis=1))
    monkeypatch.setattr(ring_serial, 'save_unwrapped_seal', save)
    return {'color': 'red', 'region_id': 'seal-0', 'inputs': [], 'reads': []}


@pytest.mark.parametrize('reverse,forward,status', [
    ('5001038010311', ('5001038010311', .55), 'recognized'),
    ('5001038010311', ('1103108301005', .96), 'ambiguous'),
    ('5O01038010311', ('1103108301005', .60), 'unreadable'),
    ('500103', ('1103108301005', .60), 'unreadable'),
    ('37080 230570 21', ('3721', .86), 'recognized'),
    ('37080\u3000230570\t21', ('3721', .86), 'recognized'),
    ('37O80 230570 21', ('3721', .86), 'unreadable'),
    ('37080-230570-21', ('3721', .86), 'unreadable'),
    ('37080 230570', ('3721', .86), 'unreadable'),
    ('37080 230570 21', ('3708023057022', .96), 'ambiguous'),
])
def test_accept_only_confident_unambiguous_digits(monkeypatch, tmp_path, reverse, forward, status):
    artifact = prepare(monkeypatch, tmp_path)

    class Reader:
        def read_line(self, path, *, provider):
            text, score = (reverse, .99) if path.stem.endswith('-180') else forward
            return [TextObservation(text, score, 0, 0, 1, 1)]

    ring_serial.read_ring_serial(tmp_path/'source.png', tmp_path, 0, '/files/test', artifact,
                                reader=Reader(), provider='paddle_v6')
    assert artifact['ring_serial_detection']['status'] == status
    if status == 'recognized':
        assert artifact['ring_serial_text'] == ''.join(reverse.split())
        selected = artifact['ring_serial_detection']['candidates'][1]
        assert selected['text'] == reverse
        assert selected['normalized_text'] == artifact['ring_serial_text']
        assert artifact['reads'][0]['channel'] == 'serial'
        assert artifact['reads'][0]['matching'] is False
        assert artifact['reads'][0]['image_url'].endswith('serial-180.png')
    else:
        assert 'ring_serial_text' not in artifact
        assert artifact['reads'] == []


@pytest.mark.parametrize('required_serial', ['', '5001038010311', '5001038010312'])
def test_serial_is_separate_unless_requirement_requests_it(monkeypatch, tmp_path, required_serial):
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), np.full((200, 200, 3), 255, np.uint8))
    directory = tmp_path/'artifacts'/'seals'
    directory.mkdir(parents=True)
    cv2.imwrite(str(directory/'seal-0-ring-input.png'), strip())
    artifact = prepare(monkeypatch, tmp_path)
    artifact.update(shape_code='round', index=0, errors=[])
    entry = {'id': 'seal-0:ring', 'channel': 'ring', 'image_url': '/files/ring.png'}
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *args: (artifact, [(entry, source)]))

    class Reader:
        def read_line(self, path, *, provider):
            text, score = ('示例有限公司', .99) if path == source else (
                ('5001038010311', .99) if path.stem.endswith('-180') else ('051', .50))
            return [TextObservation(text, score, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, 'OcrReader', Reader)
    texts, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, 'red', '客户章', 0)],
        tmp_path/'artifacts', '/files/test', 'paddle_v6', orientation_mode='none',
        requirement='示例有限公司' + required_serial)
    assert artifacts[0]['ring_serial_text'] == '5001038010311'
    assert texts == (['示例有限公司', '示例有限公司5001038010311']
                     if required_serial else ['示例有限公司'])


def test_complete_ring_opt_in_keeps_company_default(monkeypatch, tmp_path):
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), np.full((120, 120, 3), (80, 80, 240), np.uint8))
    calls = []
    monkeypatch.setattr(unwrap, '_save_ellipse_annulus_unwrapped',
                        lambda *args, **kwargs: calls.append(kwargs['trim_whitespace']) or True)
    unwrap.save_unwrapped_seal(source, tmp_path/'company.png', None,
                               elliptical=True, normalized=True, single_line=True)
    unwrap.save_unwrapped_seal(source, tmp_path/'serial.png', None,
                               elliptical=True, normalized=True, single_line=True,
                               preserve_full_ring=True)
    assert calls == [True, False]


@pytest.mark.parametrize('phase', [0, 300, 1200])
def test_broken_strokes_join_for_location_only(phase):
    image = strip()
    _, _, stats, _ = cv2.connectedComponentsWithStats((image[:, :, 0] < 180).astype(np.uint8), 8)
    digits = sorted((x, y, w, h) for x, y, w, h, area in stats[1:]
                    if x < 650 and y < 55 and h > 20)
    for x, y, w, h in digits[:5]:
        image[y+h//2-2:y+h//2+3, x:x+w] = 255
    image = np.roll(image, phase, axis=1)
    original = image.copy()
    found = ring_serial.locate_ring_serial(image)
    assert found['glyph_count'] == 13
    assert found['stroke_join_height'] > 0
    left, top, right, bottom = found['crop_box']
    crop = np.roll(image, found['shift'], axis=1)[top:bottom, left:right]
    assert np.count_nonzero(crop[:, :, 0] < 180) == np.count_nonzero(original[:55, :, 0] < 180)
    assert np.array_equal(image, original)
