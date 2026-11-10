"""Whole-page stamp localization, independently of stamp text recognition."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging import detection, seal_detector as model
from receipt_ocr.runtime.calls import capture_recognition_calls


def output(*rows):
    return np.asarray(rows, dtype=np.float32).reshape(-1, 5).T[None]


@pytest.mark.parametrize('shape,padding', [((1000, 500), (160, 0)), ((500, 1000), (0, 160))])
def test_letterbox_preserves_aspect_and_converts_bgr_to_rgb(shape, padding):
    image = np.full((*shape, 3), (25, 100, 250), dtype=np.uint8)
    tensor, scale, actual_padding = model._preprocess(image)
    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32 and tensor.flags.c_contiguous
    assert scale == .64 and actual_padding == padding
    np.testing.assert_allclose(tensor[0, :, 320, 320], np.array([250, 100, 25]) / 255)
    np.testing.assert_allclose(tensor[0, :, 0, 0], 114 / 255)
    np.testing.assert_array_equal(image[0, 0], [25, 100, 250])


@pytest.mark.parametrize('shape,padding,row,expected', [
    ((1000, 500), (160, 0), [288, 192, 128, 128, .9], (100, 200, 300, 400)),
    ((500, 1000), (0, 160), [192, 288, 128, 128, .9], (200, 100, 400, 300)),
])
@pytest.mark.parametrize('transposed', [False, True])
def test_boxes_return_to_original_pixels_for_both_output_layouts(shape, padding, row, expected, transposed):
    raw = output(row)
    if transposed:
        raw = raw.transpose(0, 2, 1)
    boxes = model._postprocess(raw, shape, .64, padding)
    assert len(boxes) == 1 and boxes[0].xyxy == expected
    assert boxes[0].confidence == pytest.approx(.9)


def test_low_scores_and_duplicate_boxes_are_removed():
    boxes = model._postprocess(output(
        [200, 200, 200, 200, .9], [210, 210, 200, 200, .8],
        [500, 500, 50, 50, .95], [50, 50, 30, 30, .5],
    ), (640, 640), 1, (0, 0))
    assert {box.xyxy for box in boxes} == {(100, 100, 300, 300), (475, 475, 525, 525)}


def test_invalid_candidates_and_boxes_outside_the_page_are_discarded():
    boxes = model._postprocess(output(
        [-16, 320, 64, 640, .9], [-100, -100, 20, 20, .95],
        [50, 50, 0, 20, .9], [50, 50, -5, 20, .9],
        [np.inf, 50, 20, 20, .9], [50, 50, 20, 20, np.nan],
    ), (640, 640), 1, (0, 0))
    assert [box.xyxy for box in boxes] == [(0, 0, 16, 640)]
    assert model._postprocess(output(), (640, 640), 1, (0, 0)) == []


@pytest.mark.parametrize('raw', [np.zeros((5, 8400)), np.zeros((1, 6, 8400))])
def test_incompatible_model_output_fails_instead_of_silent_empty_result(raw):
    with pytest.raises(ValueError):
        model._postprocess(raw, (640, 640), 1, (0, 0))


def test_model_boxes_keep_geometry_without_color_size_or_page_position_filters(monkeypatch, tmp_path):
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    image[100:140, 800:850] = (30, 30, 220)  # Small upper-page red stamp.
    image[700:800, 100:200] = (220, 30, 30)  # Displaced blue customer stamp.
    source = tmp_path / '中文回单.png'
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    source.write_bytes(encoded.tobytes())
    monkeypatch.setattr(detection, 'detect_seal_boxes', lambda image: [
        model.SealDetection((100, 700, 200, 800), .9),
        model.SealDetection((800, 100, 850, 140), .9),
        model.SealDetection((100, 500, 200, 600), .9),
    ])
    monkeypatch.setattr(detection, '_detect_color_seal_regions', lambda image: pytest.fail('color fallback'))
    regions = detection.detect_seal_regions(source)
    assert [(r.x, r.y, r.width, r.height) for r in regions] == [
        (.8, .1, .05, .04), (.1, .5, .1, .1), (.1, .7, .1, .1)]
    assert [r.color for r in regions] == ['red', 'red', 'blue']
    assert [r.role for r in regions] == ['收货客户章', '发货单位章', '收货客户章']
    assert [r.pixel_ratio for r in regions] == [1, 0, 1]
    assert all(not r.qingtong_cls for r in regions)


def test_successful_empty_model_result_does_not_call_color_fallback(monkeypatch, tmp_path):
    source = tmp_path / 'empty.png'
    cv2.imwrite(str(source), np.full((100, 100, 3), 255, dtype=np.uint8))
    monkeypatch.setattr(detection, 'detect_seal_boxes', lambda image: [])
    monkeypatch.setattr(detection, '_detect_color_seal_regions', lambda image: pytest.fail('color fallback'))
    assert detection.detect_seal_regions(source) == []


@pytest.mark.parametrize('error', [FileNotFoundError('weights'), ImportError('onnxruntime'), RuntimeError('inference')])
def test_unavailable_model_or_failed_inference_uses_color_fallback(monkeypatch, tmp_path, caplog, error):
    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    source = tmp_path / 'fallback.png'
    cv2.imwrite(str(source), image)
    def fail(image):
        raise error
    monkeypatch.setattr(detection, 'detect_seal_boxes', fail)
    seen, expected = [], [object()]
    monkeypatch.setattr(detection, '_detect_color_seal_regions', lambda image: seen.append(image) or expected)
    assert detection.detect_seal_regions(source) is expected
    np.testing.assert_array_equal(seen[0], image)
    assert '回退颜色轮廓定位' in caplog.text


def test_shared_session_is_loaded_once_and_can_be_released_and_reloaded(monkeypatch):
    monkeypatch.setattr(model, '_detector', None)
    monkeypatch.setattr(model, 'seal_detector_model_path', lambda: 'weights.onnx')
    seen = []
    def create(path):
        seen.append(path)
        return object()
    monkeypatch.setattr(model, 'SealDetector', create)
    with ThreadPoolExecutor(max_workers=4) as pool:
        loaded = list(pool.map(lambda _: model.load_seal_detector(), range(8)))
    assert len(seen) == 1 and all(item is loaded[0] for item in loaded)
    assert model.seal_detector_loaded()
    model.release_seal_detector()
    assert not model.seal_detector_loaded()
    assert model.load_seal_detector() is not loaded[0] and len(seen) == 2


def test_inference_is_recorded_with_local_label_and_original_page_size(monkeypatch, tmp_path):
    source = tmp_path / 'receipt.png'
    cv2.imwrite(str(source), np.full((1000, 500, 3), 255, dtype=np.uint8))
    detector = model.SealDetector.__new__(model.SealDetector)
    detector.input_name, detector.output_name = 'images', 'output0'
    detector.session = SimpleNamespace(run=lambda names, data: [output([288, 192, 128, 128, .9])])
    monkeypatch.setattr(model, 'load_seal_detector', lambda: detector)
    with capture_recognition_calls(source) as trace:
        assert len(detection.detect_seal_regions(source)) == 1
    calls = trace.snapshot()['calls']
    assert len(calls) == 1
    assert calls[0]['method'] == 'seal_detector' and calls[0]['method_label'] == '本地印章定位'
    assert (calls[0]['width'], calls[0]['height']) == (500, 1000)
    assert calls[0]['status'] == 'succeeded'
