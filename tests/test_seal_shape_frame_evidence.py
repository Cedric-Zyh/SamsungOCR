"""Dense oval/polygon hulls still need actual rectangle-frame evidence."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.shapes import _has_rectangular_frame, seal_region_is_rectangular


@pytest.mark.parametrize('angle', [0, 17, 67, 90])
@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_eight_vertex_cut_corners_do_not_make_a_rectangle(angle, scale):
    # This octagon fills 92% of its box and has eight vertices, so the old
    # area/vertex rule accepted it despite each missing corner being large.
    points = np.array([[.2, 0], [.8, 0], [1, .2], [1, .8],
                       [.8, 1], [.2, 1], [0, .8], [0, .2]], np.float32)
    points = (points - .5) * [300*scale, 240*scale]
    t = np.deg2rad(angle)
    rotation = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    hull = (points @ rotation.T + [400*scale, 400*scale]).astype('float32')[:, None]
    rect = cv2.minAreaRect(hull)
    assert cv2.contourArea(hull) / np.prod(rect[1]) >= .84
    assert not _has_rectangular_frame(hull, rect)


@pytest.mark.parametrize('size', [(300, 300), (350, 200), (200, 350)])
@pytest.mark.parametrize('angle', [0, 23, 90])
def test_complete_frame_with_wrong_api_hint_stays_rectangular(tmp_path, size, angle):
    image = np.full((600, 600, 3), 255, np.uint8)
    corners = np.rint(cv2.boxPoints(((300, 300), size, angle))).astype('int32')
    cv2.polylines(image, [corners], True, (70, 70, 245), 8)
    # Inner text-like marks must not remove valid outer corners.
    for x in range(230, 370, 30):
        cv2.rectangle(image, (x, 285), (x+15, 315), (70, 70, 245), -1)
    source = tmp_path/'rectangle.png'
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, 'red', '客户章', 0, 'circle')
    assert seal_region_is_rectangular(source, region)


def test_round_boundary_with_table_like_noise_is_not_a_rectangle(tmp_path):
    image = np.full((500, 600, 3), 255, np.uint8)
    cv2.ellipse(image, (300, 250), (240, 180), 0, 0, 360, (70, 70, 245), 8)
    for y in [145, 245, 350]:
        cv2.line(image, (80, y), (520, y), (140, 140, 245), 2)
    source = tmp_path/'noisy-oval.png'
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, 'red', '客户章', 0, 'rectangle')
    assert not seal_region_is_rectangular(source, region)


def test_one_occluded_side_keeps_a_clear_three_sided_rectangle(tmp_path):
    image = np.full((400, 600, 3), 255, np.uint8)
    # A U-shaped surviving frame retains its four corner/end positions.
    cv2.polylines(image, [np.array([[70, 310], [70, 80], [530, 80], [530, 310]])],
                  False, (70, 70, 245), 8)
    source = tmp_path/'occluded-rectangle.png'
    cv2.imwrite(str(source), image)
    region = SealRegion(0, 0, 1, 1, 'red', '客户章', 0, 'rectangle')
    assert seal_region_is_rectangular(source, region)
