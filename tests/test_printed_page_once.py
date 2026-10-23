"""One color-clean page read supplies requirements; preview crops add no OCR."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from receipt_ocr.application.context import DocumentContext
from receipt_ocr.application.requests import StageRequest
from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.runtime.scope import provider_scope
from receipt_ocr.stages import fields
from receipt_ocr.stages import date, seal
from types import SimpleNamespace


def row(text, x=.04, y=.47, width=.75, height=.018, confidence=.99):
    return TextObservation(text, confidence, x, y, width, height)


class Reader:
    def __init__(self, rows):
        self.rows, self.inputs, self.paths = rows, [], []

    def recognize(self, path, **options):
        with Image.open(path) as image:
            self.inputs.append(image.copy())
        self.paths.append(Path(path))
        return self.rows

    def recognize_line(self, *args, **kwargs):
        raise AssertionError('requirement must reuse the page reading')

    def detect_boxes(self, *args, **kwargs):
        raise AssertionError('requirement must reuse the page detection')


def context_for(tmp_path, rows):
    source = tmp_path / 'receipt.png'
    image = Image.new('RGB', (1000, 1200), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 500, 350, 590), fill=(255, 50, 50))
    draw.rectangle((100, 540, 130, 565), fill=(70, 70, 70))
    image.save(source)
    reader = Reader(rows)
    context = DocumentContext(source, text_recognizer=reader)
    context.document_type = {'type': 'receipt', 'reliable': True, 'confidence': .99}
    context._qr = ''
    return context, reader, source


def request(tmp_path):
    return StageRequest(route={k: 'paddle_v6' for k in ('page', 'date', 'seal')},
                        artifact_dir=tmp_path / 'artifacts', artifact_url_prefix='/artifacts')


def test_page_read_is_clean_full_size_cached_and_does_not_modify_original(tmp_path):
    context, reader, source = context_for(tmp_path, [row('签章要求：客户收货专用章（1）')])
    original = source.read_bytes()
    context.page('paddle_v6')
    result = fields.recognize(context, request(tmp_path))
    again = context.page('paddle_v6')
    assert len(reader.inputs) == 1
    assert reader.inputs[0].size == (1000, 1200)
    assert reader.inputs[0].getpixel((50, 550)) == 255
    assert reader.inputs[0].getpixel((110, 550)) == 70
    assert source.read_bytes() == original
    assert context.source == source.resolve()
    assert not reader.paths[0].exists()  # Temporary page was released.
    assert result.fields['签章要求'] == '客户收货专用章（1）'
    assert '去印章色整页识别' in result.metadata['签章要求']['source']
    again.clear()
    assert context.cached_page('paddle_v6')
    artifact = result.requirement_artifacts[0]
    assert artifact['ocr_scope'] == 'clean_page'
    assert artifact['ocr_rows'][0]['text'] == '签章要求：客户收货专用章（1）'
    with Image.open(tmp_path / 'artifacts/requirements/signature-requirement.png') as saved:
        expected = reader.inputs[0].crop(artifact['crop_box'])
        assert np.array_equal(np.asarray(saved), np.asarray(expected))


def test_long_requirement_keeps_same_line_station_phone_but_not_next_row_quantity(tmp_path):
    observations = [
        row('签章要求：三星电子服务中心取机专用章（2）', width=.49),
        row('6237143站电话：02081061101', x=.54, width=.42),
        row('签收说明：整单完整签收', y=.495, width=.35),
        row('实收数量：', x=.57, y=.495, width=.12),
    ]
    context, reader, _ = context_for(tmp_path, observations)
    result = fields.recognize(context, request(tmp_path))
    assert result.fields['签章要求'] == '三星电子服务中心取机专用章（2）6237143站电话：02081061101'
    assert result.requirement_artifacts[0]['crop_box'][2] > 960
    assert len(reader.inputs) == 1


def test_missing_label_is_not_reconstructed_from_customer_or_middle_repeat(tmp_path):
    context, reader, _ = context_for(tmp_path, [
        row('客户名称：某客户公司', y=.2),
        row('某客户公司收货专用章（1）', y=.33),
        row('签收说明：整单完整签收', y=.49),
    ])
    result = fields.recognize(context, request(tmp_path))
    assert result.fields['签章要求'] == ''
    assert result.requirement_artifacts == []
    assert len(reader.inputs) == 1


def test_forbidden_provider_never_prepares_or_reads_the_page(tmp_path, monkeypatch):
    context, reader, _ = context_for(tmp_path, [])
    monkeypatch.setattr(context, 'printed_page_image', lambda: pytest.fail('must check permission first'))
    with provider_scope({'other'}), pytest.raises(ValueError):
        context.page('paddle_v6')
    assert not reader.inputs


def test_failed_provider_can_retry_once_and_next_read_uses_successful_cache(tmp_path):
    context, reader, _ = context_for(tmp_path, [row('签章要求：客户收货专用章')])
    recognize = reader.recognize
    attempts = []

    def unstable(path, **options):
        attempts.append(Path(path))
        if len(attempts) == 1:
            raise RuntimeError('unavailable')
        return recognize(path, **options)

    reader.recognize = unstable
    with pytest.raises(RuntimeError):
        context.page('paddle_v6')
    assert context.page('paddle_v6')
    assert context.page('paddle_v6')
    assert len(attempts) == 2
    assert all(not path.exists() for path in attempts)


def test_date_and_seal_receive_original_color_source_after_page_cleaning(tmp_path, monkeypatch):
    context, reader, source = context_for(tmp_path, [row('签章要求：客户收货专用章')])
    fields.recognize(context, request(tmp_path))
    seen = []

    def check_original(path):
        assert Path(path) == source.resolve()
        with Image.open(path) as original:
            assert original.getpixel((50, 550)) == (255, 50, 50)

    def date_reader(path, *args, **options):
        check_original(path)
        seen.append('date')
        return [], []

    def seal_reader(path, *args, **options):
        check_original(path)
        seen.append('seal')
        return [], []

    monkeypatch.setattr(seal, 'detect_seal_regions', lambda path: check_original(path) or [])
    date.recognize(context, request(tmp_path), date_reader)
    seal.recognize(context, request(tmp_path), seal_reader,
                   SimpleNamespace(skipped=lambda: {'enabled': False}))
    assert seen == ['date', 'seal']
    assert len(reader.inputs) == 1
