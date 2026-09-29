"""Provider boundaries: scope, stage capabilities and persisted-plan migration."""

import subprocess
import sys

import pytest

from receipt_ocr.providers import catalog as ocr_backends
from receipt_ocr.providers import paddle_runtime as paddle_ocr
from receipt_ocr.application import plans as recognition_config
from receipt_ocr.providers import PROVIDERS, ProviderDefinition, ProviderRegistry
from receipt_ocr.providers.paddle import PaddleTextProvider
from receipt_ocr.runtime.scope import provider_scope


def test_registry_import_does_not_load_models_or_application():
    subprocess.run(
        [sys.executable, "-B", "-c", """
import sys
from receipt_ocr.providers import PROVIDERS
assert PROVIDERS.require('paddle').id == 'paddle_v6'
for name in ('paddle', 'paddleocr', 'receipt_ocr.providers.paddle_runtime', 'flask',
             'receipt_ocr.web.application', 'receipt_ocr.application.plans'):
    assert name not in sys.modules, name
"""], check=True,
    )


@pytest.mark.parametrize("legacy", ["paddle", "paddle_server", "hybrid", "vision", "hybrid_server"])
def test_saved_legacy_ids_use_only_the_remaining_model(legacy, monkeypatch):
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: [
        {"id": "paddle_v6", "available": True},
    ])
    assert ocr_backends.resolve_backend(legacy) == "paddle_v6"
    assert ocr_backends.backend_route(legacy) == dict.fromkeys(
        ("page", "date", "seal"), "paddle_v6",
    )
    assert "PP-OCRv5" not in repr(paddle_ocr.MODEL_VARIANTS)


def test_dedicated_seal_provider_never_replaces_page_ocr(monkeypatch):
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: [
        {"id": "paddle_seal", "available": True},
    ])
    assert ocr_backends.backend_route("paddle_seal") == {
        "page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_seal",
    }
    with pytest.raises(ValueError, match="通用文字识别"):
        PaddleTextProvider("paddle_seal").recognize("unused.jpg")
    assert ocr_backends.recognize_text("unused.jpg", backend="paddle_seal") == []


def test_adapter_authorizes_the_physical_provider_before_inference(monkeypatch):
    calls = []
    monkeypatch.setattr(paddle_ocr, "recognize_text", lambda path, **kw: calls.append((path, kw)) or [])
    provider = PaddleTextProvider("paddle")
    with provider_scope({"qingtong"}):
        assert provider.recognize("unused.jpg") == []
    assert calls == []
    with provider_scope({"paddle_v6"}):
        provider.recognize("unused.jpg", model_variant="server", max_side=1000)
    assert calls == [("unused.jpg", {"model_variant": "v6", "max_side": 1000})]


@pytest.mark.parametrize("method,stage,allowed", [
    ("paddle_v6", "fields", True),
    ("paddle_v6", "date", True),
    ("paddle_seal", "seal", True),
    ("paddle_seal", "date", False),
    ("paddle_seal", "products", False),
    ("qingtong", "seal", True),
    ("qingtong", "fields", False),
    ("danzhengtong", "handwriting", True),
    ("danzhengtong", "products", False),
    ("unknown", "fields", False),
])
def test_plan_validation_uses_stage_capabilities(monkeypatch, method, stage, allowed):
    monkeypatch.setattr(recognition_config, "backend_catalog", lambda: [
        {"id": name, "available": True} for name in ("paddle_v6", "paddle_seal")
    ])
    if allowed:
        assert recognition_config.validate_config({stage: [method]})[stage] == [method]
    else:
        with pytest.raises(ValueError):
            recognition_config.validate_config({stage: [method]})


def test_remote_credential_and_local_availability_gates_are_preserved(monkeypatch):
    monkeypatch.setattr(recognition_config, "backend_catalog", lambda: [])
    with pytest.raises(ValueError, match="接口密钥"):
        recognition_config.validate_config({"seal": ["qingtong"]}, api_enabled=False)
    with pytest.raises(ValueError, match="不可用"):
        recognition_config.validate_config({"fields": ["paddle_v6"]})
    assert recognition_config.validate_config({"fields": ["danzhengtong"]})["fields"] == ["danzhengtong"]


def test_registry_rejects_ambiguous_ids_and_invalid_routes():
    definition = ProviderDefinition("text", "Text", frozenset({"fields"}), "local_text")
    with pytest.raises(ValueError, match="重复"):
        ProviderRegistry([definition, definition])
    with pytest.raises(ValueError, match="别名"):
        ProviderRegistry([definition], aliases={"old": "missing"})
    with pytest.raises(ValueError, match="整页"):
        ProviderRegistry([ProviderDefinition(
            "seal", "Seal", frozenset({"seal"}), "local_seal", page_fallback="missing",
        )])
