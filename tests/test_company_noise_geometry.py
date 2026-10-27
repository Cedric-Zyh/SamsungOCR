"""Image-only proposals preserve glyphs; OCR agreement is a separate gate."""
import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.company_noise import propose_company_noise_trim


def _blank(width):
    return np.full((100, width), 255, np.uint8)


def _glyph():
    image = _blank(50)
    for x in (8, 38):
        cv2.line(image, (x, 10), (x, 80), 0, 5, cv2.LINE_AA)
    for y in (15, 45, 75):
        cv2.line(image, (8, y), (38, y), 0, 5, cv2.LINE_AA)
    return image


def _text(*, repeated_first=0, repeated_tail=0):
    # Repeat actual glyph pixels rather than labeling or normalizing text.
    glyph = _glyph()
    count = 10 + repeated_first + 2 * repeated_tail
    pieces = [_blank(10)]
    for _ in range(count):
        pieces.extend((glyph, _blank(16)))
    pieces.append(_blank(10))
    return np.concatenate(pieces, axis=1)


def _cloud():
    image = _blank(140)
    # Staggered disconnected patches have uninterrupted column support.
    for row, y in enumerate(range(12, 88, 12)):
        for x in range(2 + (row % 2) * 6, 135, 12):
            cv2.rectangle(image, (x, y), (min(139, x+5), y+5), 0, -1, cv2.LINE_AA)
    return image


def _strip(*, left=False, right=False, repeated_first=0, repeated_tail=0):
    text = _text(repeated_first=repeated_first, repeated_tail=repeated_tail)
    pieces = [_cloud(), _blank(20)] if left else []
    text_left = 160 if left else 0
    pieces.append(text)
    if right:
        pieces.extend((_blank(20), _cloud()))
    image = np.concatenate(pieces, axis=1)
    yy, xx = np.where(text < 100)
    protected = [text_left + int(xx.min()), text_left + int(xx.max()) + 1]
    return image, protected


@pytest.mark.parametrize('left,right', [(True, True), (True, False), (False, True)])
def test_only_terminal_clouds_are_removed_and_full_height_is_kept(left, right):
    image, protected = _strip(left=left, right=right)
    before = image.copy()
    result = propose_company_noise_trim(image)
    assert result['accepted'] is True
    x0, y0, x1, y1 = result['bounds']
    assert (y0, y1) == (0, image.shape[0])
    assert x0 <= protected[0] and x1 >= protected[1]
    assert x0 > 0 if left else x0 == 0
    assert x1 < image.shape[1] if right else x1 == image.shape[1]
    # Every proposed cut lies in a full-height blank band.
    if left:
        assert np.all(image[:, x0] == 255)
    if right:
        assert np.all(image[:, x1] == 255)
    np.testing.assert_array_equal(image, before)


@pytest.mark.parametrize('repeated_first,repeated_tail', [(0, 0), (1, 0), (2, 0),
                                                       (0, 1), (0, 2), (1, 1)])
def test_real_repeated_first_and_tail_glyphs_are_not_noise(repeated_first, repeated_tail):
    image, _ = _strip(repeated_first=repeated_first, repeated_tail=repeated_tail)
    result = propose_company_noise_trim(image)
    assert result['accepted'] is False
    assert result['bounds'] == [0, 0, image.shape[1], image.shape[0]]
    assert result['measurements']['reason'] == 'no_terminal_cloud'


def test_repeated_glyphs_inside_outer_clouds_are_retained():
    image, protected = _strip(left=True, right=True, repeated_first=2, repeated_tail=2)
    result = propose_company_noise_trim(image)
    assert result['accepted'] is True
    assert result['bounds'][0] <= protected[0]
    assert result['bounds'][2] >= protected[1]


@pytest.mark.parametrize('side', ['left', 'right'])
def test_tightly_spaced_repeated_tall_strokes_are_not_a_cloud(side):
    # Four tightly spaced 川-shaped glyphs have no large projection gap and
    # many components. Their twelve tall strokes still belong to real text.
    repeated = _blank(130)
    for glyph in range(4):
        for stroke in range(3):
            x = 5 + 30 * glyph + 10 * stroke
            cv2.line(repeated, (x, 10), (x, 80), 0, 5, cv2.LINE_AA)
    pieces = [repeated, _blank(20), _text()] if side == 'left' else [
        _text(), _blank(20), repeated]
    image = np.concatenate(pieces, axis=1)
    before = image.copy()
    result = propose_company_noise_trim(image)
    assert result['accepted'] is False
    assert result['bounds'] == [0, 0, image.shape[1], image.shape[0]]
    assert result['measurements']['reason'] == 'terminal_glyph_support'
    support = result['measurements']['probes'][1]['terminal_glyph_support']
    assert support[0 if side == 'left' else 1]['tall_components'] == 12
    np.testing.assert_array_equal(image, before)


@pytest.mark.parametrize('side', ['left', 'right'])
def test_a_real_tall_edge_stroke_joined_to_noise_is_protected(side):
    cloud = _cloud()
    # The word-ending stroke shares column support with the noise. Its height
    # and proximity to retained text prevent silently removing that glyph.
    x = 130 if side == 'left' else 8
    cv2.line(cloud, (x, 10), (x, 80), 0, 5, cv2.LINE_AA)
    pieces = [cloud, _blank(20), _text()] if side == 'left' else [
        _text(), _blank(20), cloud]
    image = np.concatenate(pieces, axis=1)
    result = propose_company_noise_trim(image)
    assert result['accepted'] is False
    assert result['bounds'] == [0, 0, image.shape[1], image.shape[0]]
    assert result['measurements']['reason'] == 'terminal_glyph_support'


def test_glyph_support_at_one_end_rejects_the_whole_trim_proposal():
    prefix = _cloud()
    cv2.line(prefix, (130, 10), (130, 80), 0, 5, cv2.LINE_AA)
    image = np.concatenate([prefix, _blank(20), _text(), _blank(20), _cloud()], axis=1)
    result = propose_company_noise_trim(image)
    assert result['accepted'] is False
    assert result['bounds'] == [0, 0, image.shape[1], image.shape[0]]
    assert result['measurements']['reason'] == 'terminal_glyph_support'


def test_missing_full_height_whitespace_rejects_core_only_gaps():
    image, _ = _strip(left=True, right=True)
    image[-1, :] = 0
    before = image.copy()
    result = propose_company_noise_trim(image)
    assert result['accepted'] is False
    assert result['bounds'] == [0, 0, image.shape[1], image.shape[0]]
    assert result['measurements']['reason'] == 'missing_full_height_gap'
    np.testing.assert_array_equal(image, before)


def test_faint_gap_bridge_rejects_threshold_sensitive_boundary():
    image, _ = _strip(left=True)
    # A faint bottom stroke appears only above the Otsu threshold and moves
    # the full-height separator. The central text itself stays unchanged.
    image[96:99, 140:160] = 110
    result = propose_company_noise_trim(image)
    assert result['accepted'] is False
    assert result['bounds'] == [0, 0, image.shape[1], image.shape[0]]
    measurements = result['measurements']
    assert measurements['reason'] == 'unstable_threshold_boundaries'
    assert len(measurements['probes']) == 3
    assert max(measurements['boundary_spreads']) > measurements['stability_tolerance']


@pytest.mark.parametrize('image', [None, np.zeros((0, 10), np.uint8),
                                 np.zeros((10, 0), np.uint8), np.zeros((10,), np.uint8),
                                 np.zeros((10, 20, 3), np.uint8),
                                 np.zeros((40, 400), np.float32)])
def test_invalid_grayscale_input_is_rejected(image):
    with pytest.raises(ValueError, match='nonempty 2D uint8'):
        propose_company_noise_trim(image)


@pytest.mark.parametrize('shape', [(31, 500), (100, 499)])
def test_unsupported_strip_shape_returns_unchanged_bounds(shape):
    image = np.full(shape, 255, np.uint8)
    result = propose_company_noise_trim(image)
    assert result == {'accepted': False, 'bounds': [0, 0, shape[1], shape[0]],
                      'measurements': {'reason': 'unsupported_strip_geometry'}}
