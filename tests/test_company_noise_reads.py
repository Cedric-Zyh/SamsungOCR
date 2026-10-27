"""A geometric proposal never edits identity without real three-view agreement."""
from copy import deepcopy
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.providers import paddle_runtime
from receipt_ocr.recognition.seal.ocr import company_noise as noise
from receipt_ocr.recognition.seal.ocr.interface import OcrReader
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone
from receipt_ocr.runtime.calls import capture_recognition_calls
from receipt_ocr.runtime.progress import model_stage


NAME = '深圳测试供应链管理有限公司成都分公司'
OLD = '海' + NAME


def observation(text=NAME, confidence=.99):
    return TextObservation(text, confidence, 0, 0, 1, 1)


def setup_case(tmp_path, monkeypatch):
    image = np.tile(np.arange(600, dtype=np.uint16) % 240, (100, 1)).astype(np.uint8)
    source = tmp_path / 'continuous.png'
    assert cv2.imwrite(str(source), image)
    a = {
        'region_id': 'seal-0', 'color': 'red',
        'multi_zone_layout': {'method': 'ellipse_continuous_name',
                             'expected_inner_roles': ['type', 'number'], 'arcs': {'company': {}}},
        'inputs': [{'id': 'base', 'zone_name': 'company', 'channel': 'ring',
                    'image_url': '/test/continuous.png', 'status': 'completed'}],
        'reads': [{'id': 'base:0', 'input_id': 'base', 'channel': 'ring', 'text': OLD,
                   'confidence': .98, 'image_url': '/test/continuous.png'},
                  {'channel': 'layout_inner', 'text': '收货专用章', 'confidence': .99,
                   'image_url': '/test/purpose.png'},
                  {'channel': 'layout_inner', 'text': '(1)', 'confidence': .99,
                   'image_url': '/test/number.png'}],
        'unwrapped_text': OLD, 'unwrap_texts': [OLD], 'unwrapped_url': '/test/continuous.png',
        'errors': [],
    }
    proposal = {'accepted': True, 'bounds': [50, 0, 550, 100],
                'measurements': {'reason': 'terminal_cloud_with_stable_full_height_gap'}}
    monkeypatch.setattr(noise, 'propose_company_noise_trim', lambda pixels: deepcopy(proposal))
    return a, source, image, proposal


def fake_reader(responses, calls):
    def read(path, **kwargs):
        calls.append(Path(path).name)
        result = responses[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result
    return SimpleNamespace(read=read, read_line=read)


def test_agreement_selects_only_native_pixels_and_preserves_all_actual_evidence(tmp_path, monkeypatch):
    a, source, image, _ = setup_case(tmp_path, monkeypatch)
    calls = []
    reader = fake_reader([[observation()], [observation(confidence=.98)], [observation()]], calls)
    assert noise.read_company_noise(a, source, tmp_path, 0, '/test', reader=reader, provider='paddle_v6')
    assert calls == ['continuous.png', 'seal-0-company-noise-crop.png', 'seal-0-company-noise-padding.png']
    np.testing.assert_array_equal(cv2.imread(str(source), 0), image)
    crop = cv2.imread(str(tmp_path / calls[1]), 0)
    padded = cv2.imread(str(tmp_path / calls[2]), 0)
    np.testing.assert_array_equal(crop, image[:, 50:550])
    np.testing.assert_array_equal(padded[12:-12, 12:-12], crop)
    assert np.all(padded[:12] == 255) and np.all(padded[-12:] == 255)
    assert np.all(padded[:, :12] == 255) and np.all(padded[:, -12:] == 255)
    rings = [r for r in a['reads'] if r['channel'] == 'ring']
    assert len(rings) == 1 and rings[0]['text'] == NAME
    assert a['reads'][0]['text'] == OLD and a['reads'][0]['channel'] == noise.DIAGNOSTIC_CHANNEL
    assert a['inputs'][0]['superseded_by'] == rings[0]['input_id']
    assert a['unwrapped_text'] == NAME and a['unwrap_texts'] == [NAME]
    assert a['unwrapped_url'] == rings[0]['image_url']
    assert a['company_noise']['confidence'] == .98
    assert len(a['company_noise']['input_ids']) == 3
    assert all(r.get('matching') is False for r in a['reads'] if r['channel'] == noise.DIAGNOSTIC_CHANNEL)
    assert finalize_multi_zone(a) == [NAME + '收货专用章(1)']
    assert compare_seal_text_strict(NAME + '收货专用章(2)', a['matching_texts'])['status'] == '不匹配'
    assert 'requirement' not in signature(noise.read_company_noise).parameters


@pytest.mark.parametrize('stage', [0, 1, 2])
@pytest.mark.parametrize('mode', ['empty', 'multiple', 'low', 'nan', 'inf', 'too_high', 'error'])
def test_unusable_actual_read_keeps_baseline_and_records_only_attempted_inputs(tmp_path, monkeypatch, stage, mode):
    a, source, _, _ = setup_case(tmp_path, monkeypatch)
    before = deepcopy(a)
    bad = {'empty': [], 'multiple': [observation(), observation()],
           'low': [observation(confidence=.949)], 'nan': [observation(confidence=float('nan'))],
           'inf': [observation(confidence=float('inf'))], 'too_high': [observation(confidence=1.1)],
           'error': RuntimeError('unavailable')}[mode]
    responses = [[observation()] for _ in range(3)]
    responses[stage] = bad
    calls = []
    assert not noise.read_company_noise(a, source, tmp_path, 0, '/test',
                                       reader=fake_reader(responses, calls), provider='paddle_v6')
    assert len(calls) == stage + 1
    assert [r for r in a['reads'] if r['channel'] == 'ring'] == [before['reads'][0]]
    assert a['unwrapped_text'] == OLD and a['unwrapped_url'] == before['unwrapped_url']
    assert a['inputs'][0] == before['inputs'][0]
    assert all(i['channel'] == noise.DIAGNOSTIC_CHANNEL for i in a['inputs'][1:])
    assert finalize_multi_zone(a) == [OLD + '收货专用章(1)']
    assert len(a['errors']) == (mode == 'error')


@pytest.mark.parametrize('different', ['公司公司', '不同分公司', '真实额外字'])
def test_conflicting_complete_words_cannot_enter_matching(tmp_path, monkeypatch, different):
    a, source, _, _ = setup_case(tmp_path, monkeypatch)
    calls = []
    responses = [[observation(NAME)], [observation(NAME)], [observation(NAME + different)]]
    assert not noise.read_company_noise(a, source, tmp_path, 0, '/test',
                                       reader=fake_reader(responses, calls), provider='paddle_v6')
    assert a['company_noise']['reason'] == 'conflicting_readings'
    assert finalize_multi_zone(a) == [OLD + '收货专用章(1)']
    assert all(different not in text for text in a['matching_texts'])


def test_pixel_rejection_needs_no_additional_ocr(tmp_path, monkeypatch):
    a, source, _, proposal = setup_case(tmp_path, monkeypatch)
    proposal['accepted'] = False
    calls = []
    assert not noise.read_company_noise(a, source, tmp_path, 0, '/test',
                                       reader=fake_reader([], calls), provider='paddle_v6')
    assert calls == [] and len(a['inputs']) == 1


@pytest.mark.parametrize('failed', [False, True])
def test_existing_runtime_counts_each_real_prediction_without_nested_suppression(tmp_path, monkeypatch, failed):
    a, source, _, _ = setup_case(tmp_path, monkeypatch)
    class LinePredictor:
        def predict(self, *, input):
            if failed and str(input).endswith('padding.png'):
                raise RuntimeError('failed prediction')
            return [{'rec_text': OLD if input == str(source) else NAME, 'rec_score': .99}]
    class Detector:
        def predict(self, **_):
            return [{'rec_texts': [NAME], 'rec_scores': [.99],
                     'rec_polys': [[[0, 0], [600, 0], [600, 100], [0, 100]]]}]
    monkeypatch.setattr(paddle_runtime, '_line_recognizer', lambda _: LinePredictor())
    monkeypatch.setattr(paddle_runtime, '_pipeline', lambda _: Detector())
    reader = OcrReader()
    with capture_recognition_calls(source, tmp_path, '/files/artifacts/test') as captured, model_stage('seal', 'paddle_v6'):
        assert reader.read_line(source, provider='paddle_v6')[0].text == OLD
        accepted = noise.read_company_noise(a, source, tmp_path, 0, '/test', reader=reader, provider='paddle_v6')
    assert accepted is not failed
    summary = captured.snapshot()['summary']
    assert summary['total'] == 4
    assert summary['failed'] == int(failed) and summary['succeeded'] == 4 - int(failed)
    assert a['unwrapped_text'] == (OLD if failed else NAME)


def test_optional_preparation_failure_preserves_the_new_baseline(tmp_path, monkeypatch):
    from receipt_ocr.recognition.seal.preprocess import continuous_company as company
    from test_continuous_company_recovery import evidence, setup_image
    setup_image(tmp_path, monkeypatch)
    a = evidence()
    def unavailable(*args, **kwargs):
        raise OSError('cannot save optional image')
    monkeypatch.setattr(company, 'read_company_noise', unavailable)
    reader = SimpleNamespace(read_line=lambda *a, **k: [observation(OLD)])
    assert company.read_continuous_company(a, tmp_path, 0, '/test', reader=reader, provider='paddle_v6')
    assert a['company_noise']['status'] == 'failed'
    assert a['unwrapped_text'] == OLD
    assert len([r for r in a['reads'] if r['channel'] == 'ring']) == 1
    assert finalize_multi_zone(a) == [OLD + '收货专用章(1)']


@pytest.mark.parametrize('failed_name', ['crop', 'padding'])
def test_real_helper_image_save_failure_cannot_discard_the_baseline(tmp_path, monkeypatch, failed_name):
    from receipt_ocr.recognition.seal.preprocess import continuous_company as company
    from test_continuous_company_recovery import evidence, setup_image
    _, _, pixels, _ = setup_case(tmp_path, monkeypatch)
    setup_image(tmp_path, monkeypatch)
    a = evidence()
    monkeypatch.setattr(company, 'continuous_company_pixels', lambda *a, **k: (pixels, {
        'degrees': [90, 450], 'radial_range': [.6, .94], 'border': {'method': 'test'},
    }))
    write = cv2.imwrite
    monkeypatch.setattr(cv2, 'imwrite', lambda path, image: False if str(path).endswith(
        f'company-noise-{failed_name}.png') else write(path, image))
    reader = SimpleNamespace(read=lambda *a, **k: pytest.fail('images were not saved'),
                             read_line=lambda *a, **k: [observation(OLD)])
    assert company.read_continuous_company(a, tmp_path, 0, '/test', reader=reader, provider='paddle_v6')
    assert a['company_noise']['status'] == 'failed'
    assert a['unwrapped_text'] == OLD
    assert finalize_multi_zone(a) == [OLD + '收货专用章(1)']


@pytest.mark.parametrize('provider', ['paddle_seal', 'danzhengtong'])
def test_non_v6_path_never_runs_the_optional_noise_reader(monkeypatch, provider):
    from receipt_ocr.recognition.seal.preprocess import continuous_company as company
    from test_continuous_company_recovery import evidence
    a = evidence(); before = deepcopy(a)
    monkeypatch.setattr(company, 'read_company_noise', lambda *a, **k: pytest.fail('wrong provider'))
    assert not company.read_continuous_company(a, '/absent', 0, '', reader=None, provider=provider)
    assert a == before


def test_real_pixel_gate_and_actual_crops_work_together_without_a_requested_name(tmp_path, monkeypatch):
    from receipt_ocr.recognition.seal.preprocess.company_noise import propose_company_noise_trim
    from test_company_noise_geometry import _strip
    a, source, _, _ = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr(noise, 'propose_company_noise_trim', propose_company_noise_trim)
    image, protected = _strip(left=True, right=True, repeated_first=2, repeated_tail=1)
    assert cv2.imwrite(str(source), image)
    calls = []
    reader = fake_reader([[observation()] for _ in range(3)], calls)
    assert noise.read_company_noise(a, source, tmp_path, 0, '/test', reader=reader, provider='paddle_v6')
    left, top, right, bottom = a['company_noise']['bounds']
    assert left <= protected[0] and right >= protected[1]
    np.testing.assert_array_equal(cv2.imread(str(tmp_path / calls[1]), 0), image[top:bottom, left:right])
