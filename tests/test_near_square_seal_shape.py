import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.shapes import seal_region_shape
from receipt_ocr.recognition.seal.preprocess.shapes import classify_shape


@pytest.mark.parametrize('angle', [0, 7, 33, 90])
@pytest.mark.parametrize('size', [(300, 300), (320, 270), (270, 320)])
def test_square_and_tall_frames_use_geometry_instead_of_aspect_ratio(tmp_path, angle, size):
    image = np.full((500, 500, 3), 255, np.uint8)
    polygon = np.rint(cv2.boxPoints(((250, 250), size, angle))).astype(np.int32)
    cv2.polylines(image, [polygon], True, (150, 150, 245), 7)
    source = tmp_path/'rectangle.png'
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, 'red', '客户章', 0, qingtong_cls='ellipse')
    assert seal_region_shape(source, region) == 'rectangle'
    assert classify_shape(source, region) == 'rectangle'


@pytest.mark.parametrize('axes,expected', [((170, 170), 'circle'), ((170, 140), 'ellipse'),
                                         ((170, 90), 'ellipse')])
def test_near_square_circle_and_oval_are_not_rectangles_even_with_wrong_hint(tmp_path, axes, expected):
    image = np.full((500, 500, 3), 255, np.uint8)
    cv2.ellipse(image, (250, 250), axes, 10, 0, 360, (70, 70, 245), 7)
    source = tmp_path/'oval.png'
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, 'red', '客户章', 0, qingtong_cls='rectangle')
    assert seal_region_shape(source, region) == expected


@pytest.mark.parametrize('color,ink', [('red', (70, 70, 245)), ('blue', (245, 70, 70))])
@pytest.mark.parametrize('angle', [0, 23, 75])
@pytest.mark.parametrize('axes,expected', [((170, 170), 'circle'), ((170, 160), 'circle'),
                                         ((170, 110), 'ellipse')])
def test_usable_local_geometry_overrides_conflicting_remote_shape(tmp_path, color, ink, angle, axes, expected):
    image = np.full((500, 600, 3), 255, np.uint8)
    cv2.ellipse(image, (300, 250), axes, angle, 0, 360, ink, 7)
    source = tmp_path/'local-shape.png'
    cv2.imwrite(str(source), image)
    hint = 'ellipse' if expected == 'circle' else 'circle'
    region = SealRegion(0, 0, 1, 1, color, '客户章', 0, qingtong_cls=hint)
    assert seal_region_shape(source, region) == expected
    assert classify_shape(source, region) == ('round' if expected == 'circle' else expected)


def test_blue_line_tails_do_not_stretch_a_locally_round_border(tmp_path):
    image = np.full((450, 700, 3), 255, np.uint8)
    cv2.circle(image, (350, 225), 170, (245, 70, 70), 6)
    image[40:44, 30:670] = (250, 230, 230)
    source = tmp_path/'round-with-scan-line.png'
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, 'blue', '客户章', 0, qingtong_cls='ellipse')
    assert seal_region_shape(source, region) == 'circle'


def test_remote_shape_is_only_a_fallback_when_local_pixels_have_no_evidence(tmp_path):
    source = tmp_path/'blank.png'
    cv2.imwrite(str(source), np.full((400, 400, 3), 255, np.uint8))
    region = SealRegion(0, 0, 1, 1, 'blue', '客户章', 0, qingtong_cls='ellipse')
    assert seal_region_shape(source, region) == 'ellipse'
