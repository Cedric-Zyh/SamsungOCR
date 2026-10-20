import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.ring_company import save_company_ring_input
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess import inputs


def _company_strip():
    image = np.full((200, 2400, 3), 255, np.uint8)
    cv2.putText(image, 'Y', (5, 180), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 6)
    cv2.putText(image, 'COMPANY', (700, 180), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 6)
    digits = np.full((55, 420, 3), 255, np.uint8)
    cv2.putText(digits, '5001038010311', (5, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 2)
    image[:55, 180:600] = cv2.rotate(digits, cv2.ROTATE_180)
    return image


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_opposite_small_number_band_is_excluded_without_resizing_company(tmp_path, scale):
    source, destination = tmp_path / 'ring.png', tmp_path / 'company.png'
    image = cv2.resize(_company_strip(), None, fx=scale, fy=scale)
    cv2.imwrite(str(source), image)
    result = save_company_ring_input(source, destination)

    assert result is not None
    assert len(result['excluded']) == 1
    assert result['excluded'][0]['glyph_count'] >= 10
    left, _, right, _ = result['excluded'][0]['box']
    assert 150 * scale < left < right < 650 * scale
    output = cv2.imread(str(destination))
    assert np.array_equal(output, np.concatenate((image[:, :left], image[:, right:]), axis=1))
    assert np.array_equal(cv2.imread(str(source)), image)


@pytest.mark.parametrize('layout', ['digits_only', 'same_size_digits', 'company_strokes_below'])
def test_ambiguous_or_company_number_layouts_are_not_removed(tmp_path, layout):
    image = _company_strip()
    if layout == 'digits_only':
        image[60:] = 255
    elif layout == 'same_size_digits':
        image[:, 150:650] = 255
        cv2.putText(image, '123', (180, 180), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 6)
    else:
        # Detached upper components can belong to company characters.
        cv2.rectangle(image, (200, 100), (570, 150), (0, 0, 0), 4)
    source, destination = tmp_path / 'ring.png', tmp_path / 'company.png'
    cv2.imwrite(str(source), image)
    assert save_company_ring_input(source, destination) is None
    assert not destination.exists()


def test_pipeline_reads_company_derivative_and_retains_full_unwrap(monkeypatch, tmp_path):
    source = tmp_path / 'source.png'
    cv2.imwrite(str(source), np.full((200, 200, 3), 255, np.uint8))
    monkeypatch.setattr(inputs, 'classify_shape', lambda *_: 'round')
    monkeypatch.setattr(inputs, 'save_unwrapped_seal',
                        lambda source, destination, *args, **kwargs: cv2.imwrite(str(destination), _company_strip()))
    raw, expected = '司重庆维立可通信设备有限公', '重庆维立可通信设备有限公司'

    class Reader:
        def read_line(self, path, *, provider):
            assert path.name == 'seal-0-ring-seam.png'
            assert cv2.imread(str(path)).shape[1] < _company_strip().shape[1]
            return [TextObservation(raw, .97, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, 'OcrReader', Reader)
    texts, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, 'red', '客户章', 0)],
        tmp_path / 'artifacts', '/files/test', 'paddle_v6',
        requirement=expected, orientation_mode='none',
    )
    artifact = artifacts[0]
    assert artifact['errors'] == []
    assert texts == [raw, expected]
    assert artifact['unwrapped_full_url'].endswith('/seal-0-ring.png')
    assert artifact['unwrapped_before_seam_url'].endswith('/seal-0-ring-company.png')
    assert artifact['unwrapped_url'].endswith('/seal-0-ring-seam.png')
    assert artifact['reads'][0]['image_url'] == artifact['unwrapped_url']
    shifted = cv2.imread(str(tmp_path / 'artifacts/seals/seal-0-ring-seam.png'))
    company = cv2.imread(str(tmp_path / 'artifacts/seals/seal-0-ring-company.png'))
    assert np.array_equal(np.roll(shifted, artifact['ring_seam_adjustment']['shift'], axis=1), company)
    full = next(item for item in artifact['inputs'] if item['name'] == 'ring-full')
    assert full['ocr_input'] is False
