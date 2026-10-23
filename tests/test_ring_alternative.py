from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess import ring_alternative
from receipt_ocr.recognition.seal.postprocess.ring_alternative import select_ring_alternative
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.stages.seal import _round_stamp_candidates


@pytest.mark.parametrize('outcome', ['unverified', 'failed'])
def test_unverified_or_failed_alternative_keeps_primary_candidates(monkeypatch, tmp_path, outcome):
    artifact = {'shape_code': 'ellipse', 'index': 0, 'color': 'red', 'errors': [],
                'reads': [], 'inputs': [], 'original_url': '/original.png',
                'color_isolated_oriented_url': '/clean.png', 'unwrapped_url': '/ring.png'}
    entry = {'id': 'seal-0:ring', 'channel': 'ring', 'image_url': '/ring.png'}
    monkeypatch.setattr(pipeline, 'prepare_inputs',
                        lambda *args: (deepcopy(artifact), [(deepcopy(entry), tmp_path/'ring.png')]))

    class Reader:
        def read_line(self, *args, **kwargs):
            return [TextObservation('福州中吴通信技术有限公司', .97, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, 'OcrReader', Reader)
    requirement = '福州中昊通信技术有限公司'

    def run():
        texts, artifacts = pipeline._recognize_local_seals(
            tmp_path/'source.png', [], [SealRegion(0, 0, 1, 1, 'red', '客户章', 0)],
            tmp_path, '/files/test', 'paddle_v6', requirement=requirement)
        candidates = seal_candidates({'fields': {'签章要求': requirement},
                                      'processing_artifacts': {'seals': artifacts}})
        return texts, artifacts[0], candidates

    monkeypatch.setattr(pipeline, 'read_normal_alternative', lambda *args, **kwargs: {})
    before_texts, before_artifact, before_candidates = run()

    def alternate(*args, **kwargs):
        if outcome == 'failed':
            raise RuntimeError('alternative unavailable')
        return {'matching': False, 'text': requirement, 'confidence': .999,
                'reads': [{'text': requirement, 'confidence': .999}]}

    monkeypatch.setattr(pipeline, 'read_normal_alternative', alternate)
    texts, actual, candidates = run()
    assert texts == before_texts
    assert candidates == before_candidates
    actual.pop('ring_normal_alternative')
    before_artifact.pop('ring_normal_alternative')
    assert actual == before_artifact


def test_alternative_reuses_masked_pixels_and_intact_border(monkeypatch, tmp_path):
    intact = np.full((80, 120, 3), 255, np.uint8)
    intact[20:60, 20:100] = (80, 80, 240)
    masked = intact.copy()
    masked[35:45] = 255
    cv2.imwrite(str(tmp_path/'intact.png'), intact)
    cv2.imwrite(str(tmp_path/'masked.png'), masked)
    before = (tmp_path/'masked.png').read_bytes()
    artifact = {'color': 'red', 'ring_input_url': '/files/masked.png',
                'ring_input_source_url': '/files/intact.png'}
    original_artifact = deepcopy(artifact)

    def trace(path, **kwargs):
        assert path.name == 'intact.png'
        return SimpleNamespace(annulus=(1, 2, 3))

    def save(gray, mask, path, **kwargs):
        assert np.all(gray[35:45] == 255)
        assert np.any(gray[20:35] < 255)
        cv2.imwrite(str(path), gray)
        return True

    monkeypatch.setattr(ring_alternative, 'trace_ellipse_border', trace)
    monkeypatch.setattr(ring_alternative, '_ellipse_normal_maps', lambda *args: (None, None))
    monkeypatch.setattr(ring_alternative, '_save_ellipse_annulus_unwrapped', save)
    reader = SimpleNamespace(read_line=lambda *args, **kwargs: [
        TextObservation('备选文字', .99, 0, 0, 1, 1)])
    result = ring_alternative.read_normal_alternative(
        tmp_path, 0, '/files', artifact, reader=reader, provider='paddle_v6')
    assert result['text'] == '备选文字'
    assert result['matching'] is False
    assert artifact == original_artifact
    assert (tmp_path/'masked.png').read_bytes() == before


def artifact_case(primary='洛限东利通信有限公司', alternate='洛阳东利通信有限公司',
                  center='业务受理(2)'):
    return {
        'shape': '椭圆', 'shape_code': 'ellipse', 'region_id': 'seal-0', 'index': 0,
        'color': 'red', 'original_url': '/original.png',
        'color_isolated_oriented_url': '/clean.png',
        'unwrapped_text': primary, 'unwrapped_raw_text': primary,
        'unwrapped_url': '/primary.png', 'round_type_band_text': center,
        'ring_reorder': {'text': primary}, 'inputs': [], 'reads': [], 'errors': [],
        'ring_normal_alternative': {
            'status': 'completed', 'matching': False, 'region_id': 'seal-0',
            'text': alternate, 'image_url': '/alternate.png',
            'reads': [{'text': alternate, 'confidence': .9718}],
        },
    }


def test_correct_alternative_wins_and_matches_in_pipeline_and_standard_picker(monkeypatch, tmp_path):
    requirement = '洛阳东利通信有限公司业务受理（2）'
    artifact = artifact_case()
    alternate = deepcopy(artifact.pop('ring_normal_alternative'))
    entry = {'id': 'seal-0:ring', 'channel': 'ring', 'image_url': '/primary.png'}
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *a: (
        deepcopy(artifact), [(deepcopy(entry), tmp_path/'ring.png')]))
    monkeypatch.setattr(pipeline, 'OcrReader', lambda: SimpleNamespace(read_line=lambda *a, **kw: [
        TextObservation('洛限东利通信有限公司', .98, 0, 0, 1, 1)]))
    monkeypatch.setattr(pipeline, 'read_normal_alternative', lambda *a, **kw: deepcopy(alternate))
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path/'source.png', [], [SealRegion(0, 0, 1, 1, 'red', '客户章', 0)],
        tmp_path, '/files/test', 'paddle_v6', requirement=requirement)
    selected = artifacts[0]
    candidates = _round_stamp_candidates(texts, artifacts)
    check = compare_seal_text_strict(requirement, candidates, mismatch_over_partial=True)
    assert check['status'] == '匹配'
    assert check['recognized'] == '洛阳东利通信有限公司业务受理(2)'
    assert selected['unwrapped_text'] == '洛阳东利通信有限公司'
    assert selected['unwrapped_url'] == '/alternate.png'
    assert selected['ring_primary']['text'] == '洛限东利通信有限公司'
    assert selected['ring_primary']['image_url'] == '/primary.png'
    assert selected['reads'][0]['text'] == '洛限东利通信有限公司'
    assert selected['ring_normal_alternative']['reads'] == alternate['reads']
    registration = seal_candidates({'fields': {'签章要求': requirement},
                                   'processing_artifacts': {'seals': artifacts}})
    assert registration[0]['text_status'] == '匹配'
    assert registration[0]['recognized'] == check['recognized']


@pytest.mark.parametrize('change', ['wrong_company', 'wrong_number', 'missing_number',
                                   'missing_center', 'partial', 'weak', 'invalid_confidence',
                                   'other_region', 'inconsistent_text', 'simulated'])
def test_alternative_never_invents_a_complete_match(change):
    artifact = artifact_case()
    alternative = artifact['ring_normal_alternative']
    if change == 'wrong_company':
        alternative['text'] = alternative['reads'][0]['text'] = '洛限东利通信有限公司'
    elif change == 'wrong_number':
        artifact['round_type_band_text'] = '业务受理(3)'
    elif change == 'missing_number':
        artifact['round_type_band_text'] = '业务受理'
    elif change == 'missing_center':
        artifact['round_type_band_text'] = ''
    elif change == 'partial':
        alternative['text'] = alternative['reads'][0]['text'] = '洛阳东利通信有限公'
    elif change == 'weak':
        alternative['reads'][0]['confidence'] = .4
    elif change == 'invalid_confidence':
        alternative['reads'][0]['confidence'] = float('nan')
    elif change == 'other_region':
        alternative['region_id'] = 'seal-1'
    elif change == 'inconsistent_text':
        alternative['reads'][0]['text'] = '洛限东利通信有限公司'
    else:
        alternative['simulated'] = True
    primary = artifact['unwrapped_text']
    extra = select_ring_alternative(artifact, '洛阳东利通信有限公司业务受理（2）')
    assert extra == []
    assert artifact['unwrapped_text'] == primary
    assert 'matching_texts' not in artifact
    assert alternative['matching'] is False


def test_primary_stays_selected_when_it_already_matches():
    artifact = artifact_case(primary='洛阳东利通信有限公司')
    extra = select_ring_alternative(artifact, '洛阳东利通信有限公司业务受理（2）')
    assert extra == ['洛阳东利通信有限公司业务受理(2)']
    assert artifact['ring_candidate_selection']['selected'] == 'primary'
    assert artifact['unwrapped_url'] == '/primary.png'
    assert artifact['ring_normal_alternative']['matching'] is True


def test_alternative_cyclic_seam_can_move_but_missing_glyphs_cannot_be_added():
    artifact = artifact_case(alternate='东利通信有限公司洛阳')
    extra = select_ring_alternative(artifact, '洛阳东利通信有限公司业务受理（2）')
    assert extra == ['洛阳东利通信有限公司业务受理(2)']
    assert artifact['unwrapped_text'] == '洛阳东利通信有限公司'
    assert artifact['unwrapped_raw_text'] == '东利通信有限公司洛阳'
    assert artifact['ring_normal_alternative']['text'] == '东利通信有限公司洛阳'
