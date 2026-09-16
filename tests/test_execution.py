from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from receipt_ocr.execution import measure, recognition_run, recognize_page
from receipt_ocr.recognition_scope import provider_scope


def test_page_cache_is_per_run_backend_and_source():
    calls = []
    def recognize(path, **kw):
        calls.append((path, kw['backend']))
        return [{'text': kw['backend']}]

    @recognition_run
    def run():
        rows = recognize_page('one.jpg', 'paddle', recognize)
        rows[0]['text'] = 'modified by stage'
        assert recognize_page('one.jpg', 'paddle', recognize) == [{'text': 'paddle'}]
        recognize_page('one.jpg', 'vision', recognize)
        recognize_page('two.jpg', 'paddle', recognize)
        with provider_scope({'vision'}):
            assert recognize_page('one.jpg', 'paddle', recognize) == []
        return {}

    for _ in range(2):
        result = run()['processing_timings']
        assert result['page_ocr_cache_hits'] == 1
        assert result['steps']['page_ocr']['calls'] == 3
    assert len(calls) == 6


def test_empty_page_is_reused_but_failed_page_is_not():
    attempts = 0
    def recognize(*a, **kw):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('temporary failure')
        return []

    @recognition_run
    def run():
        with pytest.raises(RuntimeError):
            recognize_page('one.jpg', 'paddle', recognize)
        assert recognize_page('one.jpg', 'paddle', recognize) == []
        assert recognize_page('one.jpg', 'paddle', recognize) == []
        return {}

    assert run()['processing_timings']['page_ocr_cache_hits'] == 1
    assert attempts == 2


def test_concurrent_receipts_do_not_share_cache_or_timings():
    barrier = Barrier(2)
    @recognition_run
    def run(label):
        def recognize(*a, **kw):
            barrier.wait(timeout=5)
            return [label]
        assert recognize_page('same.jpg', 'paddle', recognize) == [label]
        assert recognize_page('same.jpg', 'paddle', recognize) == [label]
        with measure(label):
            pass
        return {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ('first', 'second')))
    for label, other, result in zip(('first', 'second'), ('second', 'first'), results):
        steps = result['processing_timings']['steps']
        assert steps['page_ocr']['calls'] == 1
        assert label in steps and other not in steps


def test_failed_run_releases_its_context():
    calls = []
    def recognize(*a, **kw):
        calls.append(True)
        return []
    @recognition_run
    def run(fail):
        recognize_page('one.jpg', 'paddle', recognize)
        if fail:
            raise RuntimeError('stage failed')
        return {}
    with pytest.raises(RuntimeError):
        run(True)
    assert run(False)['processing_timings']['page_ocr_cache_hits'] == 0
    assert len(calls) == 2
