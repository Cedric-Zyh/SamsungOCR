"""Exercise the same plan, injected OCR, and resource scope through both inputs."""
from types import SimpleNamespace
from copy import deepcopy
import numpy as np
import pytest

from receipt_ocr.application import assembly, plans
from receipt_ocr.application.stage_coordinator import ReceiptAnalyzer
from receipt_ocr.application.recognition_service import RecognitionService
from receipt_ocr.application.document_service import DocumentRecognitionService
from receipt_ocr.imaging import io
from receipt_ocr.providers import text
from receipt_ocr.recognition.date.contracts import PreparedDateInput
from receipt_ocr.recognition.date.ocr.interface import DateOcrReader
from receipt_ocr.recognition.seal.ocr.interface import OcrReader


class Reader:
    def __init__(self):
        self.calls = []

    def recognize(self, path, **options):
        self.calls.append("page")
        return []

    def recognize_line(self, path, **options):
        self.calls.append("line")
        return []

    def detect_boxes(self, path, **options):
        self.calls.append("boxes")
        return []

    def recognize_seal(self, path):
        self.calls.append("seal")
        return []


@pytest.fixture
def unified(tmp_path, monkeypatch):
    source = tmp_path / "receipt.png"
    source.touch()
    reader = Reader()
    analyzer = ReceiptAnalyzer(SimpleNamespace(enabled=False), text_recognizer=reader)
    decoded, caches = [], []
    monkeypatch.setattr(plans, "backend_catalog", lambda: [{"id": "paddle_v6", "available": True}])
    monkeypatch.setattr(io, "_decode_image", lambda p: decoded.append(p) or np.zeros((2, 2)))

    def stage(context, name, request):
        caches.append(context.image_cache)
        assert text.current_text_recognizer() is reader
        io._read_image(source)
        context.page(request.route["page"])
        output = {"processing_artifacts": {}}
        if name == "fields":
            output["fields"] = {"要求到货": "2026-10-04", "签章要求": "客户收货章"}
        elif name == "date":
            image = PreparedDateInput("date", "tight", source, "color_clean", "decision", "日期")
            assert DateOcrReader().read(image, provider="paddle_v6").status == "empty"
            output["date_check"] = {"status": "匹配", "reliable": True, "actual": "2026-10-04"}
        elif name == "seal":
            seal = OcrReader()
            seal.read(source, provider="paddle_seal")
            seal.detect_boxes(source, provider="paddle_v6")
            seal.read_line(source, provider="paddle_v6")
            output["seal_check"] = {"status": "匹配", "reliable": True, "recognized": "客户收货章"}
        return output

    monkeypatch.setattr(analyzer, "run_stage", stage)
    complete = assembly.complete_result
    def finalize(output, **options):
        assert text.current_text_recognizer() is reader
        io._read_image(source)
        return complete(output, **options)
    monkeypatch.setattr(assembly, "complete_result", finalize)
    monkeypatch.setattr(assembly, "annotate_image", lambda *a: io._read_image(source))
    return RecognitionService(analyzer), source, reader, decoded, caches


def test_default_and_explicit_plans_share_results_ocr_and_one_decode(unified):
    service, source, reader, decoded, caches = unified
    default = service.recognize(source, "preview.png", ocr_backend="paddle_v6", seal_recognition_mode="local")
    calls = list(reader.calls)
    reader.calls.clear()
    explicit = service.recognize(source, "preview.png", ocr_backend="paddle_v6", seal_recognition_mode="local",
                                 recognition_config=plans.default_plan("paddle_v6", "local"))
    for result in (default, explicit):
        result.pop("processing_seconds")
        result.pop("processing_timings")
    assert default == explicit
    assert calls == reader.calls == ["page", "line", "seal", "boxes", "line"]
    assert len(decoded) == 2  # Includes stages, finalization, and preview in each run.
    assert all(not cache.images for cache in caches)
    assert io._active_cache.get() is None
    assert text.current_text_recognizer() is not reader


def test_finalization_error_releases_run_resources(unified, monkeypatch):
    service, source, reader, decoded, caches = unified
    def fail(*args, **kwargs):
        assert io._active_cache.get() is not None
        raise RuntimeError("preview failed")
    monkeypatch.setattr(assembly, "annotate_image", fail)
    with pytest.raises(RuntimeError, match="preview failed"):
        service.recognize(source, "preview.png", ocr_backend="paddle_v6", seal_recognition_mode="local")
    assert len(decoded) == 1
    assert all(not cache.images for cache in caches)
    assert io._active_cache.get() is None
    assert text.current_text_recognizer() is not reader


def test_document_service_preserves_retry_identity_and_generates_unique_outputs(tmp_path):
    source = tmp_path / "input.png"
    source.touch()
    calls = []
    def analyze(*args, **options):
        calls.append((args, deepcopy(options)))
        return {"fields": options["previous_fields"]}
    service = DocumentRecognitionService(analyze, tmp_path / "previews", tmp_path / "artifacts", lambda: "now")
    first, preview = service.recognize(source, filename="original.jpg", created_at="first-import",
                                       previous_fields={"客户名称": "客户"}, recognition_config={"date": ["paddle_v6"]})
    second, other = service.recognize(source, filename="original.jpg", previous_fields={})
    assert first["created_at"] == "first-import"
    assert first["updated_at"] == second["created_at"] == "now"
    assert preview != other
    assert first["preview_url"].endswith(preview)
    assert calls[0][1]["artifact_url_prefix"].endswith(calls[0][1]["artifact_dir"].name)
    assert calls[0][1]["filename"] == "original.jpg"
