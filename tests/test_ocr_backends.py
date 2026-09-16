import os

import pytest

from receipt_ocr import ocr_backends
from receipt_ocr import paddle_ocr


AVAILABLE = [
    {"id": "vision", "label": "macOS Vision", "available": True, "reason": ""},
    {"id": "paddle", "label": "PaddleOCR PP-OCRv5 Mobile", "available": True, "reason": ""},
    {"id": "paddle_server", "label": "PaddleOCR PP-OCRv5 Server（大模型）", "available": True, "reason": ""},
    {"id": "hybrid", "label": "混合 OCR", "available": True, "reason": ""},
    {"id": "hybrid_server", "label": "混合 OCR Server", "available": True, "reason": ""},
]


def test_paddle_runtime_disables_openmp_shared_memory_by_default():
    assert os.environ["KMP_USE_SHM"] == "0"
    assert paddle_ocr.MODEL_VARIANTS["server"][1] == "PP-OCRv5_server_rec"


def test_macos_defaults_to_hybrid_when_paddle_and_vision_are_available(monkeypatch):
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr(ocr_backends.platform, "system", lambda: "Darwin")
    assert ocr_backends.default_backend() == "hybrid"


def test_windows_defaults_to_paddle(monkeypatch):
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr(ocr_backends.platform, "system", lambda: "Windows")
    assert ocr_backends.default_backend() == "paddle"


@pytest.mark.parametrize("configured", ["auto", " AUTO "])
@pytest.mark.parametrize("system, expected", [("Darwin", "hybrid"), ("Windows", "paddle")])
def test_auto_environment_uses_available_default(monkeypatch, configured, system, expected):
    monkeypatch.setenv("OCR_BACKEND", configured)
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr(ocr_backends.platform, "system", lambda: system)

    assert ocr_backends.default_backend() == expected
    assert ocr_backends.resolve_backend(None) == expected
    assert ocr_backends.resolve_backend("auto") == expected


def test_explicit_environment_backend_still_overrides_platform_default(monkeypatch):
    monkeypatch.setenv("OCR_BACKEND", "paddle_server")
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr(ocr_backends.platform, "system", lambda: "Darwin")

    assert ocr_backends.default_backend() == "paddle_server"


def test_windows_catalog_never_exposes_macos_vision(monkeypatch):
    def fake_find_spec(name):
        return object() if name in {"Vision", "paddle", "paddleocr"} else None

    monkeypatch.setattr(ocr_backends.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ocr_backends.importlib.util, "find_spec", fake_find_spec)

    catalog = {item["id"]: item for item in ocr_backends.backend_catalog()}

    assert catalog["vision"]["available"] is False
    assert catalog["paddle"]["available"] is True
    assert catalog["hybrid"]["route"] == {
        "page": ocr_backends.BACKEND_LABELS["paddle"],
        "date": ocr_backends.BACKEND_LABELS["paddle"],
        "seal": ocr_backends.BACKEND_LABELS["paddle"],
    }
    assert "人工复核" in catalog["hybrid"]["safety_policy"]
    assert "跨模型" in catalog["hybrid_server"]["safety_policy"]
    assert catalog["paddle_server"]["max_page_side"] >= 640
    assert catalog["hybrid_server"]["max_page_side"] == catalog["paddle_server"]["max_page_side"]
    assert catalog["paddle"]["recommended"] is True
    assert catalog["hybrid"]["recommended"] is False
    assert "Windows 推荐默认" in catalog["paddle"]["usage_note"]
    assert "整批默认" in catalog["paddle_server"]["usage_note"]


def test_macos_falls_back_to_vision_when_paddle_is_unavailable(monkeypatch):
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    catalog = [
        dict(item, available=item["id"] == "vision") for item in AVAILABLE
    ]
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: catalog)
    monkeypatch.setattr(ocr_backends.platform, "system", lambda: "Darwin")
    assert ocr_backends.default_backend() == "vision"


def test_unavailable_backend_is_rejected(monkeypatch):
    catalog = [dict(AVAILABLE[0], available=False, reason="仅 macOS")]
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: catalog)
    with pytest.raises(RuntimeError, match="不可用"):
        ocr_backends.resolve_backend("vision")


def test_server_backend_uses_large_model(monkeypatch):
    captured = {}

    def fake_recognize(_path, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_text", fake_recognize)
    assert ocr_backends.recognize_text("unused.jpg", backend="paddle_server") == []
    assert captured["model_variant"] == "server"


def test_hybrid_routes_page_to_paddle_and_details_to_vision(monkeypatch):
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    assert ocr_backends.backend_route("hybrid") == {
        "page": "paddle", "date": "vision", "seal": "vision",
    }
    assert ocr_backends.backend_route("hybrid_server") == {
        "page": "paddle_server", "date": "vision", "seal": "vision",
    }


def test_hybrid_falls_back_to_mobile_for_details_without_vision(monkeypatch):
    without_vision = [dict(item, available=False) if item["id"] == "vision" else item for item in AVAILABLE]
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: without_vision)
    assert ocr_backends.backend_route("hybrid_server") == {
        "page": "paddle_server", "date": "paddle", "seal": "paddle",
    }


def test_windows_hybrid_date_and_seal_calls_only_paddle(monkeypatch):
    without_vision = [
        dict(item, available=False) if item["id"] == "vision" else item
        for item in AVAILABLE
    ]
    captured = []

    def fake_paddle(_path, **kwargs):
        captured.append(kwargs.get("model_variant"))
        return []

    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: without_vision)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_text", fake_paddle)

    assert ocr_backends.recognize_text("unused.jpg", backend="hybrid", stage="date") == []
    assert ocr_backends.recognize_text("unused.jpg", backend="hybrid", stage="seal") == []
    assert captured == ["mobile", "mobile"]
