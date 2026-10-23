from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.fields.fallbacks import _recover_signature_requirement
from receipt_ocr.recognition.fields.requirement_image import (
    requirement_crop_box, extend_clean_requirement,
)


def observation(text, x=.05, y=.47, width=.25, height=.015):
    return TextObservation(text, .99, x, y, width, height)


def detector(rows, search_box):
    def detect(path, **kwargs):
        return [{'text': r.text, 'confidence': r.confidence, 'points': [
            [r.x*1000-search_box[0], r.y*1000-search_box[1]],
            [(r.x+r.width)*1000-search_box[0], r.y*1000-search_box[1]],
            [(r.x+r.width)*1000-search_box[0], (r.y+r.height)*1000-search_box[1]],
            [r.x*1000-search_box[0], (r.y+r.height)*1000-search_box[1]],
        ]} for r in rows]
    return detect


def setup_image():
    image = Image.new('RGB', (1000, 1000), 'white')
    d = ImageDraw.Draw(image)
    d.rectangle((400, 469, 600, 485), fill='red')
    # A missing tail still exists in the source image, underneath seal ink.
    d.rectangle((620, 472, 650, 483), fill='black')
    return image


def search_bounds(box, anchor):
    margin = max(round(anchor.height*1000), 8)
    return box[0], box[1]-margin, 980, box[3]+margin


def test_clean_wide_search_precedes_final_crop_and_recognition(tmp_path):
    image = setup_image()
    source = tmp_path/'page.png'
    image.save(source)
    anchor = observation('签章要求：')
    fragment = observation('客户服务中心', x=.14, width=.15)
    rows = [anchor, fragment]
    box, _ = requirement_crop_box(image.size, anchor, rows)
    fresh = observation('签章要求：客户服务中心取机专用章5785258站', width=.62)
    expected = '客户服务中心取机专用章5785258站'
    seen = []
    detect = detector([fresh], search_bounds(box, anchor))

    def read_search(path, **kwargs):
        seen.append('clean_detection')
        with Image.open(path) as search:
            assert search.width > box[2]-box[0]
            assert search.getpixel((450-box[0], 475-search_bounds(box, anchor)[1])) == 255
        return detect(path, **kwargs)

    def read_final(path, **kwargs):
        seen.append('final_line_read')
        with Image.open(path) as crop:
            assert crop.width > 650-box[0]
            assert np.count_nonzero(np.asarray(crop) < 100) > 100
        return [observation('签章要求：'+expected)]

    artifacts = []
    result = _recover_signature_requirement(
        source, rows, '客户服务中心', 'paddle_v6', artifact_dir=tmp_path/'artifacts',
        artifacts=artifacts, text_recognizer=SimpleNamespace(
            supports_line=lambda _: True, detect_boxes=read_search, recognize_line=read_final))
    assert result['value'] == expected
    assert seen == ['clean_detection', 'final_line_read']
    assert artifacts[0]['extent_recovery']['status'] == 'extended'
    assert artifacts[0]['crop_boundary'] == 'color_clean_detected_line'
    assert (tmp_path/'artifacts/requirements/signature-requirement-search.png').is_file()


@pytest.mark.parametrize('kind', ['no_label', 'other_line', 'weak', 'shorter'])
def test_unverified_or_shorter_detection_cannot_change_extent(tmp_path, kind):
    image = setup_image()
    anchor = observation('签章要求：客户服务中心')
    box, _ = requirement_crop_box(image.size, anchor, [anchor])
    fresh = observation('签章要求：客户服务中心完整尾部', width=.6)
    if kind == 'no_label':
        fresh = observation('客户服务中心完整尾部', width=.6)
    elif kind == 'other_line':
        fresh = observation(fresh.text, width=.6, y=.497)
    elif kind == 'weak':
        fresh = TextObservation(fresh.text, .5, fresh.x, fresh.y, fresh.width, fresh.height)
    else:
        fresh = observation('签章要求：客户', width=.15)
    reader = SimpleNamespace(detect_boxes=detector([fresh], search_bounds(box, anchor)))
    expanded, info = extend_clean_requirement(
        image, anchor, [anchor], box, tmp_path/'search.png', recognizer=reader, backend='paddle_v6')
    assert expanded == box
    assert info['status'] == 'kept'


def test_same_line_neighbor_quantity_stops_clean_line_extension(tmp_path):
    image = setup_image()
    anchor = observation('签章要求：客户服务中心')
    box, _ = requirement_crop_box(image.size, anchor, [anchor])
    fresh = observation('签章要求：客户服务中心完整尾部', width=.45)
    quantity = observation('实收数量：', x=.53, width=.08)
    count = observation('100', x=.62, width=.04)
    reader = SimpleNamespace(detect_boxes=detector([fresh, quantity, count], search_bounds(box, anchor)))
    expanded, info = extend_clean_requirement(
        image, anchor, [anchor], box, tmp_path/'search.png', recognizer=reader, backend='paddle_v6')
    assert info['status'] == 'extended'
    assert 500 < expanded[2] < quantity.x*1000


def test_failed_detector_keeps_existing_line_input_and_records_failure(tmp_path):
    source = tmp_path/'page.png'
    setup_image().save(source)
    anchor = observation('签章要求：客户服务中心')

    def fail(*a, **kw):
        raise RuntimeError('test detector unavailable')

    artifacts = []
    result = _recover_signature_requirement(
        source, [anchor], '客户服务中心', 'paddle_v6', artifact_dir=tmp_path/'artifacts',
        artifacts=artifacts, text_recognizer=SimpleNamespace(
            supports_line=lambda _: True, detect_boxes=fail,
            recognize_line=lambda *a, **k: [observation('签章要求：客户服务中心')]))
    assert result['value'] == '客户服务中心'
    assert artifacts[0]['extent_recovery']['status'] == 'failed'
