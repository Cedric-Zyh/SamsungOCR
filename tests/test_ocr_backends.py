import os

import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.providers import catalog
from receipt_ocr.providers import paddle_runtime
from receipt_ocr.runtime.safety import _apply_single_paddle_safety


def test_current_catalog_has_only_supported_local_providers(monkeypatch):
    monkeypatch.setattr(catalog.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(catalog, "paddle_v6_supported", lambda: True)

    ids = {item["id"] for item in catalog.backend_catalog()}

    assert ids == {"paddle_v6", "paddle_seal"}
    assert catalog.default_backend() == "paddle_v6"
    assert catalog.backend_route("paddle_v6") == {
        "page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_v6",
    }


@pytest.mark.parametrize("name", ["paddle", "paddle_server", "hybrid", "hybrid_server", "vision"])
def test_retired_provider_ids_are_rejected(name, monkeypatch):
    monkeypatch.setattr(catalog, "backend_catalog", lambda: [
        {"id": "paddle_v6", "available": True, "reason": ""},
    ])
    with pytest.raises(ValueError, match="未知 OCR 引擎"):
        catalog.resolve_backend(name)


def test_seal_provider_routes_page_and_date_to_v6(monkeypatch):
    monkeypatch.setattr(catalog, "backend_catalog", lambda: [
        {"id": "paddle_seal", "available": True, "reason": ""},
    ])
    assert catalog.backend_route("paddle_seal") == {
        "page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_seal",
    }


def test_paddle_runtime_exposes_only_v6_variant():
    assert set(paddle_runtime.MODEL_VARIANTS) == {"v6"}
    assert paddle_runtime.provider_of("v6") == "paddle_v6"
    assert paddle_runtime.variant_of("paddle_v6") == "v6"
    assert paddle_runtime.variant_of("paddle") is None
    assert paddle_runtime.is_paddle_backend("paddle_v6") is True
    assert paddle_runtime.is_paddle_backend("paddle") is False


def test_paddle_runtime_defaults_to_v6():
    assert paddle_runtime.is_lightweight_backend("PaddleOCR PP-OCRv6 Small") is True
    assert paddle_runtime.is_lightweight_backend("paddle_v6") is True
    assert paddle_runtime.is_lightweight_backend("paddle") is False
    assert os.environ["KMP_USE_SHM"] == "0"


def test_single_v6_safety_keeps_exact_matches_reliable():
    date_check = {
        "required": "2026-02-06", "actual": "2026-02-06",
        "status": "匹配", "confidence": .95, "reliable": True,
    }
    seal_check = compare_seal_text_strict(
        "北京集中维修中心业务章(3)", ["北京集中维修中心业务章（3）"]
    )
    policy = _apply_single_paddle_safety(
        date_check, seal_check,
        {stage: "paddle_v6" for stage in ("page", "date", "seal")},
    )
    assert policy
    assert date_check["reliable"] is True
    assert seal_check["reliable"] is True
