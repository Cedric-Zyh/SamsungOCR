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


@pytest.mark.parametrize('outcome', ['different', 'failed'])
def test_alternative_cannot_change_primary_or_standard_candidates(monkeypatch, tmp_path, outcome):
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
