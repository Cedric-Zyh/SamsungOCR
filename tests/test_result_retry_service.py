"""Retry preparation is shared without partial admission or lost document context."""
from types import SimpleNamespace

import pytest

from receipt_ocr.application.retries import ResultRetryService, MissingRetrySource, InvalidRetrySelection


def service_for(tmp_path):
    source = tmp_path / 'one.png'
    source.write_bytes(b'input')
    records = {
        1: {'filename': 'one.png', 'stored_name': 'one.png', 'created_at': '2026-01-01',
            'fields': {'客户名称': '已确认客户'}, 'ocr_backend': 'paddle_v6', 'seal_recognition_mode': 'local'},
        2: {'filename': 'missing.png', 'stored_name': 'missing.png'},
    }
    calls = []
    database = SimpleNamespace(get_result=records.__getitem__, get_json_setting=lambda key: None,
        replace_after_retry=lambda identity, result, preview: calls.append(('save', identity, result, preview)) or result)
    def recognize(source, **kwargs):
        calls.append(('recognize', source, kwargs))
        return {'overall': '通过'}, 'preview.jpg'
    documents = SimpleNamespace(recognize=recognize)
    jobs = SimpleNamespace(create_retries=lambda task, ids, options: calls.append(('queue', ids, options)) or {'id': task})
    service = ResultRetryService(database, documents,
        lambda current: tmp_path / current['stored_name'], False, jobs, tmp_path)
    return service, calls


def test_retry_keeps_document_identity_and_previous_fields(tmp_path):
    service, calls = service_for(tmp_path)
    prepared = service.prepare(1, {})
    assert service.execute(1, prepared) == {'overall': '通过'}
    assert calls[0][2]['previous_fields']['客户名称'] == '已确认客户'
    assert calls[0][2]['filename'] == 'one.png'
    assert calls[0][2]['created_at'] == '2026-01-01'
    assert calls[1] == ('save', 1, {'overall': '通过'}, 'preview.jpg')


def test_batch_prepares_every_source_before_enqueuing(tmp_path):
    service, calls = service_for(tmp_path)
    with pytest.raises(MissingRetrySource):
        service.enqueue([1, 2], {})
    assert calls == []
    service.enqueue([1, 1], {})
    assert len(calls) == 1 and calls[0][0:2] == ('queue', [1])
    assert calls[0][2][1]['_stored_name'] == 'sample:one.png'


@pytest.mark.parametrize('ids', [None, [], [True], [0], ['1']])
def test_invalid_batch_does_not_create_jobs(tmp_path, ids):
    service, calls = service_for(tmp_path)
    with pytest.raises(InvalidRetrySelection):
        service.enqueue(ids, {})
    assert calls == []
