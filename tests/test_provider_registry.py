import subprocess
import sys

import pytest

from receipt_ocr.application import plans as recognition_config
from receipt_ocr.providers import PROVIDERS, ProviderDefinition, ProviderRegistry
from receipt_ocr.providers.paddle import PaddleTextProvider
from receipt_ocr.runtime.scope import provider_scope


def test_registry_import_does_not_load_models_or_application():
    subprocess.run(
        [sys.executable, "-B", "-c", """
import sys
from receipt_ocr.providers import PROVIDERS
assert PROVIDERS.require('paddle_v6').id == 'paddle_v6'
for name in ('paddle', 'paddleocr', 'flask',
             'receipt_ocr.web.application'):
    assert name not in sys.modules, name
"""], check=True,
    )


def test_provider_catalog_is_current_and_seal_provider_has_v6_fallback():
    assert {item.id for item in PROVIDERS} == {
        "paddle_v6", "paddle_seal", "qingtong", "danzhengtong",
    }
    assert PROVIDERS.require("paddle_seal").route() == {
        "page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_seal",
    }


def test_adapter_authorizes_the_physical_provider_before_inference(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "receipt_ocr.providers.paddle_runtime.recognize_text",
        lambda path, **kw: calls.append((path, kw)) or [],
    )
    provider = PaddleTextProvider("paddle_v6")
    with provider_scope({"qingtong"}):
        assert provider.recognize("unused.jpg") == []
    assert calls == []
    with provider_scope({"paddle_v6"}):
        provider.recognize("unused.jpg")
    assert calls == [("unused.jpg", {"model_variant": "v6"})]


@pytest.mark.parametrize("method,stage,allowed", [
    ("paddle_v6", "fields", True),
    ("paddle_v6", "date", True),
    ("paddle_seal", "seal", True),
    ("paddle_seal", "date", False),
    ("qingtong", "seal", True),
    ("qingtong", "fields", False),
    ("danzhengtong", "handwriting", True),
    ("danzhengtong", "products", False),
    ("paddle", "fields", False),
])
def test_plan_validation_uses_current_stage_capabilities(monkeypatch, method, stage, allowed):
    monkeypatch.setattr(recognition_config, "backend_catalog", lambda: [
        {"id": name, "available": True} for name in (
            "paddle_v6", "paddle_seal", "qingtong", "danzhengtong"
        )
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


def test_registry_rejects_ambiguous_ids_and_invalid_routes():
    definition = ProviderDefinition("text", "Text", frozenset({"fields"}), "local_text")
    with pytest.raises(ValueError, match="重复"):
        ProviderRegistry([definition, definition])
    with pytest.raises(ValueError, match="整页"):
        ProviderRegistry([ProviderDefinition(
            "seal", "Seal", frozenset({"seal"}), "local_seal", page_fallback="missing",
        )])
