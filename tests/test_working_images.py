from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace
import os

import cv2
import numpy as np
from PIL import Image
import pytest

from receipt_ocr.imaging import working_images as images
from receipt_ocr.imaging.crops import save_region_crop
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal.preprocess import (
    orientation_geometry as geometry,
    detection_scale,
)
from receipt_ocr.providers import paddle_runtime
from receipt_ocr.providers.text import text_recognizer_scope
from receipt_ocr.runtime.execution import recognition_run
from receipt_ocr.runtime.calls import capture_recognition_calls


def pixels(height=90, width=120):
    return np.random.default_rng(25).integers(
        0, 256, (height, width, 3), dtype=np.uint8
    )


def test_identical_rotations_reuse_pixels_across_paths_without_changing_geometry(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.png"
    array = pixels()
    cv2.imwrite(str(source), array)
    original = geometry.cv2.warpAffine
    calls = []

    def warp(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(geometry.cv2, "warpAffine", warp)
    with images.image_workspace_scope() as workspace:
        for index in range(3):
            path = tmp_path / f"rotated-{index}.png"
            geometry.rotate_stamp_image(source, path, 37.5)
            matrix, width, height = geometry._rotation_geometry(
                array.shape[1], array.shape[0], 37.5
            )
            expected = original(
                array,
                matrix,
                (width, height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255, 255, 255),
            )
            np.testing.assert_array_equal(cv2.imread(str(path)), expected)
        geometry.rotate_stamp_image(source, tmp_path / "other-angle.png", -37.5)
        assert len(calls) == 2
        assert workspace.snapshot()["transform_cache_hits"] == 2
    assert workspace.bytes == 0 and not workspace.frames and not workspace.paths


def test_write_through_cache_avoids_redecoding_and_copies_do_not_mutate_evidence(
    tmp_path, monkeypatch
):
    path = tmp_path / "input.png"
    array = pixels()
    with images.image_workspace_scope() as workspace:
        images.write_cv(path, array)
        monkeypatch.setattr(
            images.cv2,
            "imread",
            lambda *a, **k: pytest.fail("generated PNG was decoded again"),
        )
        first = images.read_cv(path)
        first[:] = 0
        np.testing.assert_array_equal(images.read_cv(path), array)
        with images.open_rgb(path) as image:
            np.testing.assert_array_equal(
                np.asarray(image), cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
            )
        images.write_cv(path, array)
        assert workspace.stats["file_writes"] == 1
        assert workspace.stats["writes_avoided"] == 1


def test_external_overwrite_invalidates_pixels_even_with_restored_mtime(tmp_path):
    source = tmp_path / "input.png"
    first = pixels()
    second = 255 - first
    with images.image_workspace_scope():
        images.write_cv(source, first)
        stat = source.stat()
        cv2.imwrite(str(source), second)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        np.testing.assert_array_equal(images.read_cv(source), second)
        np.testing.assert_array_equal(images.predictor_input(source), second)


def test_virtual_images_spill_before_eviction_and_memory_is_bounded(tmp_path):
    first = pixels(20, 20)
    second = 255 - first
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    with images.image_workspace_scope(max_bytes=1300) as workspace:
        images.write_temporary(a, first)
        assert not a.exists()
        images.write_temporary(b, second)
        assert a.is_file()
        np.testing.assert_array_equal(images.read_cv(a), first)
        np.testing.assert_array_equal(images.read_cv(b), second)
        assert workspace.bytes <= workspace.max_bytes
        assert workspace.stats["memory_spills"] >= 1
    assert not workspace.frames


def test_custom_text_adapters_keep_real_temporary_files(tmp_path):
    from receipt_ocr.recognition.seal.ocr.interface import write_temporary_input

    destination = tmp_path / "custom.png"
    with images.image_workspace_scope(), text_recognizer_scope(SimpleNamespace()):
        write_temporary_input(destination, pixels())
        assert destination.is_file()
        np.testing.assert_array_equal(cv2.imread(str(destination)), pixels())


def test_persisting_a_virtual_image_always_creates_a_real_file(tmp_path):
    destination = tmp_path / "input.png"
    array = pixels()
    with images.image_workspace_scope():
        images.write_temporary(destination, array)
        assert not destination.exists()
        images.write_cv(destination, array)
        assert destination.is_file()
        np.testing.assert_array_equal(cv2.imread(str(destination)), array)


def test_copy_of_original_keeps_the_original_readers_exif_behavior(tmp_path):
    source = tmp_path / "original.jpg"
    destination = tmp_path / "copy.png"
    image = Image.fromarray(pixels())
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    with images.image_workspace_scope():
        images.read_cv(source)
        images.copy_image(source, destination)
        with Image.open(destination) as original, images.open_rgb(
            destination
        ) as cached:
            np.testing.assert_array_equal(
                np.asarray(cached), np.asarray(original.convert("RGB"))
            )


def test_source_decoder_reuses_pixels_without_an_encode_decode_round_trip(
    tmp_path, monkeypatch
):
    from receipt_ocr.imaging import io

    source = tmp_path / "original.png"
    array = pixels()
    cv2.imwrite(str(source), array)
    original = np.fromfile
    reads = []

    def decode(*args, **kwargs):
        reads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(io.np, "fromfile", decode)
    with images.image_workspace_scope():
        np.testing.assert_array_equal(io._read_image(source), array)
        np.testing.assert_array_equal(io._read_image(source), array)
    assert len(reads) == 1


def test_virtual_model_inputs_keep_physical_call_metadata_and_actual_saved_input(
    tmp_path, monkeypatch
):
    seen = []

    class Predictor:
        def predict(self, *, input):
            assert isinstance(input, np.ndarray)
            seen.append(input.copy())
            input[:] = 0
            return [
                {
                    "rec_texts": ["收货专用章"],
                    "rec_scores": [0.99],
                    "rec_polys": [[[0, 0], [80, 0], [80, 20], [0, 20]]],
                }
            ]

    monkeypatch.setattr(paddle_runtime, "_pipeline", lambda _: Predictor())
    monkeypatch.setattr(paddle_runtime, "paddle_engine", lambda: "onnxruntime")
    array = pixels()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    evidence = tmp_path / "artifacts"
    evidence.mkdir()

    @recognition_run
    def run():
        a, b = scratch / "a.png", scratch / "b.png"
        images.write_temporary(a, array)
        images.write_temporary(b, array)
        one = paddle_runtime.detect_text_boxes(a)
        two = paddle_runtime.detect_text_boxes(b)
        assert one == two and not a.exists() and not b.exists()
        images.copy_image(a, evidence / "actual.png")
        return {}

    with capture_recognition_calls(
        artifact_dir=evidence, artifact_url_prefix="/files/artifacts/test"
    ) as calls:
        result = run()
    assert len(seen) == 1
    np.testing.assert_array_equal(seen[0], array)
    np.testing.assert_array_equal(cv2.imread(str(evidence / "actual.png")), array)
    trace = calls.snapshot()
    assert trace["summary"]["total"] == 1 and trace["summary"]["cache_hits"] == 1
    assert (trace["calls"][0]["width"], trace["calls"][0]["height"]) == (120, 90)
    assert "image_url" not in trace["calls"][0]
    assert (
        result["processing_timings"]["image_processing_cache"]["array_inference_inputs"]
        == 1
    )


def test_resize_is_reused_and_full_resolution_fallback_is_preserved(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.png"
    array = pixels(700, 900)
    cv2.imwrite(str(source), array)
    from receipt_ocr.recognition.seal.ocr import interface

    seen = []

    def detect(path, **kwargs):
        seen.append(images.cached_size(path) or Image.open(path).size)
        return []

    monkeypatch.setattr(interface, "detect_boxes", detect)
    with images.image_workspace_scope() as workspace:
        for i in range(2):
            scratch = tmp_path / f"trial-{i}"
            scratch.mkdir()
            detection_scale.detect_orientation_boxes(
                source, scratch, model_variant="v6"
            )
        assert seen == [(512, 398), (900, 700)] * 2
        assert workspace.stats["resize_computations"] == 1
        assert workspace.stats["transform_cache_hits"] == 1


def test_lossy_original_crop_still_uses_the_actual_saved_jpeg_pixels(tmp_path):
    source = tmp_path / "original.png"
    array = pixels()
    cv2.imwrite(str(source), array)
    output = tmp_path / "original-crop.png"
    with images.image_workspace_scope():
        save_region_crop(source, output, SealRegion(0, 0, 1, 1, "red", "test", 0))
        assert images.buffered_key(output) is None
        decoded = cv2.imread(str(output))
        assert not np.array_equal(decoded, array)
        np.testing.assert_array_equal(images.read_cv(output), decoded)


def test_parallel_transform_reuse_and_failed_scope_cleanup_are_isolated():
    array = pixels()
    with images.image_workspace_scope() as workspace:

        def work():
            return geometry.rotate_stamp_pixels(array, 90)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda context: context.run(work),
                    [copy_context() for _ in range(8)],
                )
            )
        assert workspace.stats["rotation_computations"] == 1
        assert workspace.stats["transform_cache_hits"] == 7
    with pytest.raises(RuntimeError):
        with images.image_workspace_scope() as failed:
            geometry.rotate_stamp_pixels(array, 90)
            raise RuntimeError("stage failed")
    assert failed.bytes == 0 and images.current_workspace() is None
