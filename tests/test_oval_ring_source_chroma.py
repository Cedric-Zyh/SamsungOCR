"""An alternative ring view must preserve evidence and independently agree."""
from dataclasses import replace
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.ring_border import trace_ellipse_border
from receipt_ocr.recognition.seal.preprocess.oval_ring import prepare_oval_ring
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry


@pytest.fixture
def seal(tmp_path):
    image = np.full((800, 1000, 3), 255, np.uint8)
    cv2.ellipse(image, (500, 400), (440, 330), 0, 0, 360, (100, 100, 240), 5)
    image[200:220, 400:420] = (20, 20, 20)  # Neutral form ink.
    image[200:220, 500:520] = (230, 230, 250)  # Faint stamp ink.
    image[450:480, 450:550] = (120, 120, 240)  # Horizontal row.
    source = tmp_path / 'source.png'
    cv2.imwrite(str(source), image)
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[450:480, 450:550] = 255
    mask_path = tmp_path / 'mask.png'
    cv2.imwrite(str(mask_path), mask)
    return source, mask_path, trace_ellipse_border(source)


def reader_for(texts=('海口群嘉电器有限公司',) * 2, scores=(.98, .99)):
    calls = []
    def read(path, **kwargs):
        index = len(calls)
        calls.append(path)
        return [SimpleNamespace(text=texts[index], confidence=scores[index])]
    return SimpleNamespace(read_line=read, calls=calls)


def run(seal, tmp_path, reader, **kwargs):
    source, mask, guide = seal
    return prepare_oval_ring(
        source, SealRegion(0, 0, 1, 1, 'red', '客户章', 0),
        kwargs.pop('working', source), kwargs.pop('mask', mask),
        kwargs.pop('guide', guide), kwargs.pop('rotations', []),
        tmp_path / 'ring.png', reader=reader, provider='paddle_v6', **kwargs)


def test_source_ink_and_exact_mask_preserved(seal, tmp_path):
    source, mask, guide = seal
    originals = [p.read_bytes() for p in (source, mask)]
    before_maps = guide.map_x.copy()
    result = run(seal, tmp_path, reader_for())
    assert result is not None
    raw = cv2.imread(str(result['source']))
    covered = cv2.imread(str(result['input']))
    assert np.all(raw[210, 410] == 255)  # Black does not become red.
    assert 200 < raw[210, 510, 0] < 255  # Pale red survives.
    coverage = cv2.imread(str(mask), 0) > 0
    assert np.all(covered[coverage] == 255)
    assert np.array_equal(covered[~coverage], raw[~coverage])
    assert result['evidence']['selected_gain'] == 2.4
    assert [p.read_bytes() for p in (source, mask)] == originals
    assert np.array_equal(guide.map_x, before_maps)
    assert result['ring'].is_file()


@pytest.mark.parametrize('texts,scores', [
    (('海口群嘉电器有限公司', '海群嘉电器有限公司'), (.98, .999)),
    (('海口群嘉电器有限公司',) * 2, (.96, .999)),
    (('', ''), (1., 1.)),
])
def test_reject_disagreement_even_if_wrong_result_has_higher_score(seal, tmp_path, texts, scores):
    assert run(seal, tmp_path, reader_for(texts, scores)) is None
    assert not (tmp_path / 'ring-input.png').exists()


@pytest.mark.parametrize('reason', ['elongated', 'unknown_rotation', 'wrong_mask_frame'])
def test_unsafe_frames_or_narrow_oval_do_not_invoke_ocr(seal, tmp_path, reason):
    reader = reader_for()
    kwargs = {}
    if reason == 'elongated':
        guide = seal[2]
        outer, inner, estimated = guide.annulus
        narrow = (outer[0], outer[1], ((500., 400.), (880., 440.), 0.))
        kwargs['guide'] = replace(guide, annulus=(narrow, inner, estimated))
    elif reason == 'unknown_rotation':
        kwargs['rotations'] = None
    else:
        bad = tmp_path / 'bad-mask.png'
        cv2.imwrite(str(bad), np.zeros((20, 20), np.uint8))
        kwargs['mask'] = bad
    assert run(seal, tmp_path, reader, **kwargs) is None
    assert not reader.calls


def test_rotations_replay_canvas_and_keep_mask_in_final_frame(seal, tmp_path):
    source, mask, _ = seal
    image = cv2.imread(str(source))
    coverage = cv2.imread(str(mask), 0)
    # Coarse + fine rotations must retain each expanded canvas.
    rotations = [90., -7.]
    for angle in rotations:
        h, w = image.shape[:2]
        matrix, width, height = _rotation_geometry(w, h, angle)
        image = cv2.warpAffine(image, matrix, (width, height),
                               flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
        coverage = cv2.warpAffine(coverage, matrix, (width, height), flags=cv2.INTER_NEAREST)
    working, final_mask = tmp_path / 'rotated.png', tmp_path / 'rotated-mask.png'
    cv2.imwrite(str(working), image)
    cv2.imwrite(str(final_mask), coverage)
    guide = trace_ellipse_border(working)
    result = run(seal, tmp_path, reader_for(), working=working, mask=final_mask,
                 guide=guide, rotations=rotations)
    assert result is not None
    raw, covered = [cv2.imread(str(result[key])) for key in ('source', 'input')]
    assert raw.shape == image.shape
    assert np.all(covered[coverage > 0] == 255)
    assert np.array_equal(raw[coverage == 0], covered[coverage == 0])
