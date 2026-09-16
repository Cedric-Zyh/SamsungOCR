from pathlib import Path

from PIL import Image

from receipt_ocr import paddle_ocr
import pytest


class FakeResult(dict):
    pass


def test_prediction_failure_releases_lock_and_records_inference(tmp_path, monkeypatch):
    from receipt_ocr.execution import recognition_run
    source = tmp_path / 'input.png'
    Image.new('RGB', (40, 20), 'white').save(source)
    class Failing:
        def predict(self, **kw):
            raise RuntimeError('native prediction failed')
    monkeypatch.setattr(paddle_ocr, '_line_recognizer', lambda variant: Failing())

    @recognition_run
    def run():
        with pytest.raises(RuntimeError):
            paddle_ocr.recognize_line(source)
        assert not paddle_ocr._PREDICT_LOCK.locked()
        return {}

    steps = run()['processing_timings']['steps']
    assert steps['paddle_line_inference']['calls'] == 1
    assert steps['paddle_queue_wait']['calls'] == 1


class FakePipeline:
    def __init__(self, seen: dict):
        self.seen = seen

    def predict(self, *, input: str):
        path = Path(input)
        with Image.open(path) as image:
            self.seen["size"] = image.size
        self.seen["path"] = path
        return [FakeResult(
            rec_texts=["测试"],
            rec_scores=[.99],
            rec_polys=[[[140, 70], [700, 70], [700, 140], [140, 140]]],
        )]


def test_server_page_is_memory_bounded_without_changing_normalized_boxes(
    tmp_path, monkeypatch
):
    source = tmp_path / "large.jpg"
    Image.new("RGB", (2800, 1400), "white").save(source)
    seen = {}
    monkeypatch.setenv("PADDLE_SERVER_MAX_SIDE", "1400")
    monkeypatch.setattr(paddle_ocr, "_pipeline", lambda _variant: FakePipeline(seen))

    rows = paddle_ocr.recognize_text(source, model_variant="server")

    assert seen["size"] == (1400, 700)
    assert seen["path"] != source
    assert not seen["path"].exists()
    assert len(rows) == 1
    assert rows[0].x == .1
    assert rows[0].y == .1
    assert rows[0].width == .4
    assert rows[0].height == .1


def test_mobile_page_keeps_original_resolution(tmp_path, monkeypatch):
    source = tmp_path / "large.jpg"
    Image.new("RGB", (2800, 1400), "white").save(source)
    seen = {}
    monkeypatch.setattr(paddle_ocr, "_pipeline", lambda _variant: FakePipeline(seen))

    paddle_ocr.recognize_text(source, model_variant="mobile")

    assert seen["size"] == (2800, 1400)
    assert seen["path"] == source.resolve()
