from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess.double_border import double_border_pixels, read_double_border
from receipt_ocr.stages.seal import _round_stamp_candidates


def image_case(color='red', interior=True):
    image = np.full((900, 1120, 3), 255, 'uint8')
    ink = (35, 35, 255) if color == 'red' else (255, 35, 35)
    for axes in [(460, 305), (450, 295), *([(340, 185)] if interior else [])]:
        cv2.ellipse(image, (560, 450), axes, 0, 0, 360, ink, 7)
    for angle in [*range(80, 281, 20), *range(315, 406, 15)]:
        theta = np.deg2rad(angle)
        x, y = round(560-395*np.sin(theta)), round(450+240*np.cos(theta))
        cv2.rectangle(image, (x-15, y-20), (x+15, y+20), ink, -1)
    return image


def artifact():
    return {'schema_version': 2, 'index': 0, 'region_id': 'seal-0', 'shape': '椭圆',
            'shape_code': 'ellipse', 'color': 'red', 'ring_input_source_name': 'intact.png',
            'original_url': '/files/original.png', 'color_isolated_oriented_url': '/files/intact.png',
            'unwrapped_url': '/files/old.png', 'unwrapped_text': 'MMM旧环字O',
            'round_type_band_url': '/files/purpose.png', 'round_type_band_text': '维修专用章',
            'inputs': [{'id': 'ring', 'channel': 'ring', 'image_url': '/files/old.png'}],
            'reads': [{'id': 'ring:0', 'input_id': 'ring', 'channel': 'ring', 'text': 'MMM旧环字O',
                       'confidence': .90, 'image_url': '/files/old.png'},
                      {'id': 'center:0', 'channel': 'center', 'text': '维修专用章', 'confidence': .99,
                       'provider': 'paddle_v6', 'image_url': '/files/purpose.png'}], 'errors': []}


def reader_case(company='测试京东维修中心', website='www.JD.com', score=.99):
    def read(path, **kwargs):
        assert kwargs == {'provider': 'paddle_v6'}
        return [TextObservation(website if 'website' in path.name else company, score, 0, 0, 1, 1)]
    return SimpleNamespace(read_line=read)


@pytest.mark.parametrize('color', ['red', 'blue'])
def test_geometry_uses_closed_borders_and_keeps_both_side_glyphs(color):
    image = image_case(color); original = image.copy()
    pixels = double_border_pixels(image, color=color)
    assert pixels is not None
    start, end = pixels['geometry']['company_degrees']
    assert start < 80 and end > 280
    assert np.count_nonzero(pixels['company'][:, :180] < 100) > 100
    assert np.count_nonzero(pixels['company'][:, -180:] < 100) > 100
    assert pixels['company'].shape[2] == 3
    np.testing.assert_array_equal(image, original)
    assert double_border_pixels(image_case(color, interior=False), color=color) is None


def test_active_candidate_and_registration_exclude_url_but_keep_raw_evidence(tmp_path):
    image = image_case();cv2.imwrite(str(tmp_path/'intact.png'), image)
    before = (tmp_path/'intact.png').read_bytes();a = artifact();raw = deepcopy(a['reads'])
    assert read_double_border(a, tmp_path, 0, '/files/test', reader=reader_case(), provider='paddle_v6')
    assert [r['text'] for r in a['reads'][:2]] == [r['text'] for r in raw]
    assert a['reads'][0]['matching'] is False
    assert a['reads'][0]['channel'] == 'ring_diagnostic'
    assert a['website_text'] == 'www.JD.com'
    assert a['reads'][-1]['matching'] is False
    assert a['double_border_layout']['primary']['image_url'] == '/files/old.png'
    assert (tmp_path/'intact.png').read_bytes() == before
    expected = '测试京东维修中心维修专用章'
    candidates = _round_stamp_candidates([], [a])
    assert compare_seal_text_strict(expected, candidates)['status'] == '匹配'
    assert compare_seal_text_strict('另一京东维修中心维修专用章', candidates)['status'] == '不匹配'
    assert compare_seal_text_strict(expected+'www.JD.com', candidates)['status'] != '匹配'
    registration = seal_candidates({'fields': {'签章要求': expected}, 'processing_artifacts': {'seals': [a]}})
    assert registration[0]['text_status'] == '匹配'
    assert all('www.' not in text and 'MMM' not in text for text in registration[0]['texts'])


@pytest.mark.parametrize('change', ['phone', 'lower_name', 'short_url_fragment', 'weak', 'nan',
                                    'empty', 'multiple', 'error', 'latin_company'])
def test_uncertain_or_other_lower_content_keeps_original_artifact(tmp_path, change):
    cv2.imwrite(str(tmp_path/'intact.png'), image_case())
    a = artifact(); before = deepcopy(a)
    reader = reader_case(website={'phone': '020-12345678', 'lower_name': '第二分公司',
                                 'short_url_fragment': 'WWW'}.get(change, 'www.JD.com'),
                         company='JD测试维修中心' if change == 'latin_company' else '测试京东维修中心',
                         score=.7 if change == 'weak' else float('nan') if change == 'nan' else .99)
    if change in {'empty', 'multiple', 'error'}:
        def read(*args, **kwargs):
            if change == 'error':
                raise RuntimeError('OCR unavailable')
            return [] if change == 'empty' else [TextObservation('测试公司', .99, 0, 0, 1, 1)]*2
        reader = SimpleNamespace(read_line=read)
    if change == 'error':
        with pytest.raises(RuntimeError):
            read_double_border(a, tmp_path, 0, '', reader=reader, provider='paddle_v6')
    else:
        assert not read_double_border(a, tmp_path, 0, '', reader=reader, provider='paddle_v6')
    assert a == before


@pytest.mark.parametrize('change', ['round', 'rectangle', 'multi_zone', 'weak_center', 'other_provider'])
def test_unrelated_layout_does_not_run_new_ocr(tmp_path, change):
    a = artifact()
    if change in {'round', 'rectangle'}:
        a['shape_code'] = change
    elif change == 'multi_zone':
        a['multi_zone_layout'] = {'method': 'ellipse_continuous_name'}
    elif change == 'weak_center':
        a['reads'][1]['confidence'] = .79
    reader = SimpleNamespace(read_line=lambda *a, **kw: pytest.fail('unexpected OCR'))
    assert not read_double_border(a, tmp_path, 0, '', reader=reader,
                                  provider='paddle_v5' if change == 'other_provider' else 'paddle_v6')


def test_pipeline_preserves_other_stamps_and_uses_same_stamp_company_and_center(tmp_path, monkeypatch):
    directory = tmp_path/'seals';directory.mkdir()
    cv2.imwrite(str(directory/'intact.png'), image_case())
    a = artifact(); raw = deepcopy(a['reads']);a['reads'] = [];a['inputs'] = []
    jobs = []
    for row in raw:
        entry = {'id': row['id'], 'channel': row['channel'], 'image_url': row['image_url'],
                 'detected_rows': [TextObservation(row['text'], row['confidence'], 0, 0, 1, 1).to_dict()]}
        a['inputs'].append(entry);jobs.append((entry, directory/'unused.png'))
    other = {'region_id': 'seal-1', 'shape_code': 'round', 'reads': [], 'inputs': [], 'errors': []}
    entry = {'id': 'other', 'channel': 'ring', 'image_url': '/files/other.png',
             'detected_rows': [TextObservation('其他公司', .99, 0, 0, 1, 1).to_dict()]}
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *args: (a, jobs) if args[2] == 0
                        else (other, [(entry, directory/'other.png')]))
    monkeypatch.setattr(pipeline, 'OcrReader', reader_case)
    monkeypatch.setattr(pipeline, 'read_normal_alternative', lambda *a, **kw: pytest.fail('replaced path'))
    texts, artifacts = pipeline._recognize_local_seals(tmp_path/'page.png', [], [object(), object()],
                                                       tmp_path, '/files/test', 'paddle_v6',
                                                       requirement='完全不同的签章要求')
    assert '其他公司' in texts and '测试京东维修中心' in texts and '维修专用章' in texts
    assert 'MMM旧环字O' not in texts and 'www.JD.com' not in texts
    assert artifacts[0]['unwrapped_text'] == '测试京东维修中心'
    assert not any(a['errors'] for a in artifacts)
