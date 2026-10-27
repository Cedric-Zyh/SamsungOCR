from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

from PIL import Image
import pytest

from receipt_ocr.providers import paddle_runtime
from receipt_ocr.runtime import execution
from receipt_ocr.runtime.execution import recognition_run
from receipt_ocr.runtime.scope import provider_scope


@pytest.fixture
def boxes(tmp_path, monkeypatch):
    source = tmp_path / 'source.bmp'
    Image.new('RGB', (80, 40), 'red').save(source)
    duplicate = tmp_path / 'other-path.bmp'
    duplicate.write_bytes(source.read_bytes())
    calls = []
    state = SimpleNamespace(fail=False, empty=False, error=False, rewrite=None, aba=False)

    class Pipeline:
        def __init__(self, variant):
            self.variant = variant

        def predict(self, *, input):
            content = Path(input).read_bytes()
            calls.append((self.variant, content))
            if state.fail:
                raise RuntimeError('prediction failed')
            if state.error:
                return [{'error': 'temporary provider failure'}]
            if state.rewrite is not None:
                previous = Path(input).stat()
                Path(input).write_bytes(state.rewrite)
                if state.aba:
                    observed = Path(input).read_bytes()
                    Path(input).write_bytes(content)
                    os.utime(input, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                    content = observed
            if state.empty:
                return []
            return [{'rec_texts': [sha256(content).hexdigest()[:8]], 'rec_scores': [.9],
                     'rec_polys': [[[1, 2], [61, 2], [61, 22], [1, 22]]]}]

    monkeypatch.setattr(paddle_runtime, '_pipeline', Pipeline)
    monkeypatch.setattr(paddle_runtime, 'paddle_engine', lambda: 'test-engine')
    monkeypatch.setitem(paddle_runtime.VARIANT_PROVIDERS, 'other', 'paddle_other')
    monkeypatch.setitem(paddle_runtime.MODEL_VARIANTS, 'other', ('other-det', 'other-rec'))
    return source, duplicate, calls, state


def test_identical_boxes_reuse_across_paths_without_sharing_mutable_output(boxes):
    source, duplicate, calls, _ = boxes

    @recognition_run
    def run():
        original = paddle_runtime.detect_text_boxes(source)
        original[0]['points'][0][0] = 999
        original[0]['text'] = 'caller mutation'
        reused = paddle_runtime.detect_text_boxes(duplicate)
        assert reused[0]['points'][0][0] == 1
        assert reused[0]['text'] != 'caller mutation'
        reused[0]['points'][0][0] = 777
        assert paddle_runtime.detect_text_boxes(source)[0]['points'][0][0] == 1
        return {}

    timings = run()['processing_timings']
    assert len(calls) == 1
    assert timings['text_box_ocr_cache_hits'] == 2
    assert timings['steps']['paddle_text_box_inference']['calls'] == 1


def test_rewriting_same_path_with_same_size_and_timestamp_does_not_reuse(boxes):
    source, _, calls, _ = boxes

    @recognition_run
    def run():
        before = source.stat()
        first = paddle_runtime.detect_text_boxes(source)
        Image.new('RGB', (80, 40), 'blue').save(source)
        assert source.stat().st_size == before.st_size
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        second = paddle_runtime.detect_text_boxes(source)
        assert first != second
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


def test_different_suffixes_do_not_share_provider_input_decisions(boxes):
    source, duplicate, calls, _ = boxes
    different_suffix = duplicate.with_suffix('.png')
    different_suffix.write_bytes(source.read_bytes())

    @recognition_run
    def run():
        first = paddle_runtime.detect_text_boxes(source)
        assert paddle_runtime.detect_text_boxes(different_suffix) == first
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


def test_model_identity_and_provider_permissions_are_checked_on_cache_hits(boxes):
    source, duplicate, calls, _ = boxes

    @recognition_run
    def run():
        with provider_scope({'paddle_v6'}):
            first = paddle_runtime.detect_text_boxes(source)
        with provider_scope({'paddle_other'}):
            assert paddle_runtime.detect_text_boxes(source) == []
            assert paddle_runtime.detect_text_boxes(source.parent / 'missing.bmp') == []
            assert paddle_runtime.detect_text_boxes(source, model_variant='other') == first
        with provider_scope({'paddle_v6'}):
            assert paddle_runtime.detect_text_boxes(duplicate) == first
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 1
    assert [variant for variant, _ in calls] == ['v6', 'other']


def test_engine_and_model_settings_are_part_of_cache_identity(boxes, monkeypatch):
    source, _, calls, _ = boxes

    @recognition_run
    def run():
        paddle_runtime.detect_text_boxes(source)
        monkeypatch.setattr(paddle_runtime, 'paddle_engine', lambda: 'another-engine')
        paddle_runtime.detect_text_boxes(source)
        monkeypatch.setitem(paddle_runtime.MODEL_VARIANTS, 'v6', ('different-det', 'different-rec'))
        paddle_runtime.detect_text_boxes(source)
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 3


def test_failed_predictions_are_retried_and_successful_empty_results_are_reused(boxes):
    source, _, calls, state = boxes

    @recognition_run
    def run():
        state.fail = True
        with pytest.raises(RuntimeError, match='prediction failed'):
            paddle_runtime.detect_text_boxes(source)
        state.fail, state.empty = False, True
        assert paddle_runtime.detect_text_boxes(source) == []
        assert paddle_runtime.detect_text_boxes(source) == []
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 1
    assert len(calls) == 2


def test_image_rewritten_during_prediction_is_not_cached(boxes):
    source, duplicate, calls, state = boxes
    original = source.read_bytes()
    Image.new('RGB', (80, 40), 'blue').save(duplicate)
    state.rewrite = duplicate.read_bytes()

    @recognition_run
    def run():
        paddle_runtime.detect_text_boxes(source)
        source.write_bytes(original)
        state.rewrite = None
        paddle_runtime.detect_text_boxes(source)
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


def test_provider_error_payload_keeps_old_return_behavior_without_being_cached(boxes):
    source, _, calls, state = boxes

    @recognition_run
    def run():
        state.error = True
        assert paddle_runtime.detect_text_boxes(source) == []
        state.error = False
        recovered = paddle_runtime.detect_text_boxes(source)
        assert recovered
        assert paddle_runtime.detect_text_boxes(source) == recovered
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 1
    assert len(calls) == 2


def test_aba_rewrite_with_restored_content_and_mtime_cannot_poison_cache(boxes):
    source, duplicate, calls, state = boxes
    original = source.read_bytes()
    Image.new('RGB', (80, 40), 'blue').save(duplicate)
    state.rewrite, state.aba = duplicate.read_bytes(), True

    @recognition_run
    def run():
        observed_during_rewrite = paddle_runtime.detect_text_boxes(source)
        assert source.read_bytes() == original
        state.rewrite = None
        observed_original = paddle_runtime.detect_text_boxes(source)
        assert observed_original != observed_during_rewrite
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


@pytest.mark.parametrize('change_after_version', [1, 2])
def test_file_changes_during_hash_or_cached_lookup_fall_back_to_prediction(boxes, monkeypatch,
                                                                        change_after_version):
    source, _, calls, _ = boxes
    original_version = execution._file_version
    versions = []

    def changed_version(path):
        version = original_version(path)
        versions.append(version)
        if len(versions) == change_after_version:
            Image.new('RGB', (80, 40), 'blue').save(source)
        return version

    @recognition_run
    def run():
        first = paddle_runtime.detect_text_boxes(source)
        monkeypatch.setattr(execution, '_file_version', changed_version)
        changed = paddle_runtime.detect_text_boxes(source)
        assert changed != first
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


def test_cached_lookup_rechecks_bytes_even_if_file_versions_are_unchanged(boxes, monkeypatch):
    source, _, calls, _ = boxes

    @recognition_run
    def run():
        first = paddle_runtime.detect_text_boxes(source)
        version = execution._file_version(source)
        versions = []

        def unchanged_version(path):
            versions.append(True)
            if len(versions) == 2:
                Image.new('RGB', (80, 40), 'blue').save(source)
            return version

        monkeypatch.setattr(execution, '_file_version', unchanged_version)
        assert paddle_runtime.detect_text_boxes(source) != first
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


def test_windows_change_time_rejects_aba_with_unchanged_stat_timestamps(boxes, monkeypatch):
    source, duplicate, calls, state = boxes
    Image.new('RGB', (80, 40), 'blue').save(duplicate)
    state.rewrite, state.aba = duplicate.read_bytes(), True
    initial_stat = source.stat()
    generation = [0]
    original_stat, original_write = Path.stat, Path.write_bytes

    def unchanged_stat(path, *args, **kwargs):
        return initial_stat if path == source else original_stat(path, *args, **kwargs)

    def tracked_write(path, data):
        count = original_write(path, data)
        if path == source:
            generation[0] += 1
        return count

    monkeypatch.setattr(execution, '_WINDOWS', True)
    monkeypatch.setattr(execution, '_windows_change_time', lambda path: generation[0])
    monkeypatch.setattr(Path, 'stat', unchanged_stat)
    monkeypatch.setattr(Path, 'write_bytes', tracked_write)

    @recognition_run
    def run():
        rewritten = paddle_runtime.detect_text_boxes(source)
        state.rewrite = None
        assert paddle_runtime.detect_text_boxes(source) != rewritten
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2 and generation[0] == 2


def test_unavailable_windows_change_time_falls_back_to_prediction(boxes, monkeypatch):
    source, _, calls, _ = boxes

    def unavailable(path):
        raise OSError('file change information unavailable')

    monkeypatch.setattr(execution, '_WINDOWS', True)
    monkeypatch.setattr(execution, '_windows_change_time', unavailable)

    @recognition_run
    def run():
        assert paddle_runtime.detect_text_boxes(source)
        assert paddle_runtime.detect_text_boxes(source)
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


@pytest.mark.skipif(not execution._WINDOWS, reason='Native Windows file-information API')
def test_native_windows_change_time_query_closes_its_file_handle(tmp_path):
    source = tmp_path / 'file.bmp'
    source.write_bytes(b'input')
    assert execution._windows_change_time(source) > 0
    source.unlink()
    assert not source.exists()


@pytest.mark.parametrize('failure', ['stat', 'read'])
def test_cache_file_read_failures_do_not_replace_existing_prediction_behavior(boxes, monkeypatch, failure):
    source, _, calls, _ = boxes

    def unavailable():
        raise PermissionError('cache fingerprint unavailable')

    # Only the reuse layer loses access. The original prediction path still
    # succeeds, just as it would when this optional optimization is disabled.
    proxy = SimpleNamespace(stat=unavailable if failure == 'stat' else source.stat,
                            read_bytes=unavailable)
    monkeypatch.setattr(execution, 'Path', lambda _: proxy)

    @recognition_run
    def run():
        assert paddle_runtime.detect_text_boxes(source)
        assert paddle_runtime.detect_text_boxes(source)
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(calls) == 2


@pytest.mark.parametrize('cached', [False, True])
def test_unstable_file_fallback_does_not_retry_provider_os_errors(boxes, monkeypatch, cached):
    source, _, _, _ = boxes
    attempts = []

    def failed_prediction():
        attempts.append(True)
        raise OSError('provider failed to read its input')

    @recognition_run
    def run():
        if cached:
            execution.reuse_text_boxes(source, ('test-model',), lambda: ([], True))
        versions = iter(('first', 'first', 'changed') if cached else ('first', 'changed'))
        monkeypatch.setattr(execution, '_file_version', lambda _: next(versions))
        with pytest.raises(OSError, match='provider failed'):
            execution.reuse_text_boxes(source, ('test-model',), failed_prediction)
        return {}

    assert run()['processing_timings']['text_box_ocr_cache_hits'] == 0
    assert len(attempts) == 1


def test_calls_outside_a_run_and_separate_runs_do_not_share_boxes(boxes):
    source, _, calls, _ = boxes
    paddle_runtime.detect_text_boxes(source)
    paddle_runtime.detect_text_boxes(source)
    assert len(calls) == 2

    @recognition_run
    def run():
        paddle_runtime.detect_text_boxes(source)
        paddle_runtime.detect_text_boxes(source)
        return {}

    for _ in range(2):
        assert run()['processing_timings']['text_box_ocr_cache_hits'] == 1
    assert len(calls) == 4


def test_concurrent_runs_keep_content_caches_separate(boxes):
    source, _, calls, _ = boxes
    barrier = Barrier(2)

    @recognition_run
    def run(_):
        paddle_runtime.detect_text_boxes(source)
        barrier.wait(timeout=5)
        paddle_runtime.detect_text_boxes(source)
        return {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert len(calls) == 2
    assert all(r['processing_timings']['text_box_ocr_cache_hits'] == 1 for r in results)
