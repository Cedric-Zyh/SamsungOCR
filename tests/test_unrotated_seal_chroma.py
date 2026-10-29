"""Colour cleanup must not depend on finding a horizontal rotation anchor."""
import shutil

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal.preprocess import inputs


@pytest.mark.parametrize('mode', ['none', 'polygon', 'doc_ori', 'combined', 'orientation_error'])
def test_ring_only_stamp_uses_original_chroma_in_unchanged_frame(tmp_path, monkeypatch, mode):
    source = tmp_path / 'original.png'
    image = np.full((240, 1000, 3), 255, np.uint8)
    image[40:80, 100:160] = (40, 40, 40)  # Neutral handwriting.
    image[40:80, 300:360] = (220, 220, 245)  # Faint red stamp stroke.
    image[40:80, 500:560] = (70, 70, 75)  # Red-biased black edge.
    image[40:80, 700:760] = (184, 188, 193)  # Verified grey-red stamp ink.
    cv2.imwrite(str(source), image)
    old = np.full_like(image, 255)
    old[40:80, 100:560, :2] = 20  # Existing cleanup has amplified remnants.
    monkeypatch.setattr(inputs, 'save_color_isolated_seal',
                        lambda s, d, r: cv2.imwrite(str(d), old))
    monkeypatch.setattr(inputs, 'classify_shape', lambda *a: 'round')
    # This regression checks the actual ring input pixels after orientation
    # fallback; unrelated OCR retries must not load a model during the test.
    for method in ('read', 'read_line', 'detect_boxes'):
        monkeypatch.setattr(inputs.OcrReader, method, lambda *a, **k: [])
    def orient(*args, **kwargs):
        if mode == 'orientation_error':
            raise ValueError('no usable direction')
        return None, {'status': '保留原方向'}  # No rotation_steps key.
    monkeypatch.setattr(inputs, 'prepare_round_stamp_combined', orient)
    monkeypatch.setattr(inputs, 'prepare_round_stamp', orient)
    monkeypatch.setattr(inputs, 'prepare_round_stamp_doc_ori', orient)
    monkeypatch.setattr(inputs, 'prepare_horizontal_consensus', lambda *a, **k: None)
    monkeypatch.setattr(inputs, 'save_ring_text_band_preview',
                        lambda s, d, **k: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, 'save_unwrapped_seal',
                        lambda s, d, *a, **k: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, 'save_company_ring_input', lambda *a: None)
    monkeypatch.setattr(inputs, 'save_ring_with_blank_seam', lambda *a: {'applied': False})
    directory = tmp_path / 'artifacts'
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, 'red', '客户章', 0), 0,
        directory, '/files/test', 'paddle_v6', 'combined' if mode == 'orientation_error' else mode,
    )
    assert artifact['red_enhancement']['coordinate_frame'] == 'unrotated'
    assert not artifact['orientation'].get('rotation_steps')
    assert len(jobs) == 1 and jobs[0][0]['channel'] == 'ring'
    actual = cv2.imread(str(jobs[0][1]))
    assert actual.shape == image.shape
    assert np.all(actual[60, 120] == 255)
    assert 180 < actual[60, 320, 0] < 220
    assert actual[60, 520, 0] > 230
    assert actual[60, 720, 0] < 200
    assert np.array_equal(cv2.imread(str(directory/'seal-0-color.png')), old)
    assert np.array_equal(actual, cv2.imread(str(directory/'seal-0-enhanced.png')))
    assert artifact['body_url'].endswith('seal-0-enhanced.png')
    assert artifact['ring_input_source_url'] == artifact['body_url']
    assert [e['step'] for e in artifact['errors']] == (['orientation'] if mode == 'orientation_error' else [])
