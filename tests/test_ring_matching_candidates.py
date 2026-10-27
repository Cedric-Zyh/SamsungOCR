from copy import deepcopy
from types import SimpleNamespace

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.postprocess.ring_text import reorder_ring_text
from receipt_ocr.stages.seal import _round_stamp_candidates


RING = '深圳欧瑞特供应链管理有限公司长春分公司'
RAW = '特供应链管理有限公司长春分公司深圳欧瑞'
REQUIREMENT = RING + '收货专用章(1)'


@pytest.mark.parametrize('center,status', [
    ('收货专用章', '部分匹配'),
    ('收货专用章(1)', '匹配'),
    ('收货专用章(2)', '不匹配'),
])
def test_local_ring_seam_does_not_override_missing_number(monkeypatch, tmp_path, center, status):
    inputs = [
        {'id': f'seal-0:{channel}', 'channel': channel, 'image_url': f'/{channel}.png'}
        for channel in ('center', 'ring')
    ]
    artifact = {'schema_version': 2, 'index': 0, 'region_id': 'seal-0',
                'shape': '圆形', 'shape_code': 'round', 'inputs': inputs,
                'reads': [], 'errors': []}

    class Reader:
        def read_line(self, path, *, provider):
            return [TextObservation(center if path.stem == 'center' else RAW, .99, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, 'OcrReader', Reader)
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *args, **kwargs: (
        artifact, [(item, tmp_path / f"{item['channel']}.png") for item in inputs]))
    monkeypatch.setattr(pipeline, 'recognize_receiving_form', lambda *args, **kwargs: None)
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / 'source.png', [], [SimpleNamespace()], tmp_path, '/files',
        'paddle_v6', requirement=REQUIREMENT)

    check = compare_seal_text_strict(
        REQUIREMENT, _round_stamp_candidates(texts, artifacts), mismatch_over_partial=True)
    assert check['status'] == status
    assert check['recognized'] == RING + center
    assert RAW not in texts
    assert artifacts[0]['unwrapped_raw_text'] == RAW
    assert [read['text'] for read in artifacts[0]['reads']] == [center, RAW]


def ring_artifact(center):
    return {'index': 0, 'shape': '圆形', 'shape_code': 'round', 'color': 'red',
            'original_url': '/files/ring-original.png', 'color_isolated_url': '/files/ring.png',
            'unwrapped_text': RING, 'round_type_band_text': center,
            'ring_reorder': reorder_ring_text(RAW, REQUIREMENT),
            'reads': [{'channel': 'ring', 'text': RAW, 'confidence': .99},
                      {'channel': 'center', 'text': center, 'confidence': .99}]}


def candidate(artifact):
    result = {'fields': {'签章要求': REQUIREMENT},
              'processing_artifacts': {'seals': [artifact]}}
    return seal_candidates(result)[0]


@pytest.mark.parametrize('center,status', [
    ('收货专用章', '部分匹配'),
    ('收货专用章(1)', '匹配'),
    ('收货专用章(2)', '不匹配'),
])
def test_standard_uses_same_ring_order_and_keeps_raw_evidence(center, status):
    artifact = ring_artifact(center)
    before = deepcopy(artifact)

    check = candidate(artifact)

    assert check['text_status'] == status
    assert check['recognized'] == RING + center
    assert RAW not in check['texts']
    assert artifact == before


def test_conflicting_ring_read_is_not_discarded_as_another_seam():
    artifact = ring_artifact('收货专用章')
    wrong = RING.replace('供应链', '供应练')
    artifact['reads'].append({'channel': 'ring', 'text': wrong, 'confidence': .99})

    check = candidate(artifact)

    assert check['text_status'] == '不匹配'
    assert check['recognized'] == wrong
    assert wrong in check['texts']


def test_reordered_display_without_ring_provenance_keeps_conflicting_text():
    artifact = ring_artifact('收货专用章')
    del artifact['ring_reorder']

    check = candidate(artifact)

    assert check['text_status'] == '不匹配'
    assert RAW in check['texts']
