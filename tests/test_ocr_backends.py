import os

import pytest

from receipt_ocr import ocr_backends
from receipt_ocr import paddle_ocr


# Mock catalog that mirrors the post-Vision world: only local Paddle tiers plus
# the cross-model hybrid_server route.  "hybrid" survives as a resolvable legacy
# alias (mapped to paddle) but is no longer a catalog entry.
AVAILABLE = [
    {"id": "danzhengtong", "label": "单证通", "available": True, "reason": ""},
    {"id": "paddle", "label": "PaddleOCR PP-OCRv5 Mobile", "available": True, "reason": ""},
    {"id": "paddle_server", "label": "PaddleOCR PP-OCRv5 Server（大模型）", "available": True, "reason": ""},
    {"id": "paddle_v6", "label": "PaddleOCR PP-OCRv6 Small", "available": True, "reason": ""},
    {"id": "hybrid_server", "label": "混合 OCR Server", "available": True, "reason": ""},
]


def test_paddle_runtime_disables_openmp_shared_memory_by_default():
    assert os.environ["KMP_USE_SHM"] == "0"
    assert paddle_ocr.MODEL_VARIANTS["server"][1] == "PP-OCRv5_server_rec"


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_default_backend_is_paddle_on_every_platform(monkeypatch, system):
    """The macOS special case is gone: the default is always paddle."""
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    assert ocr_backends.default_backend() == "paddle"


@pytest.mark.parametrize("configured", ["auto", " AUTO "])
@pytest.mark.parametrize("system, expected", [("Darwin", "paddle"), ("Windows", "paddle")])
def test_auto_environment_uses_available_default(monkeypatch, configured, system, expected):
    monkeypatch.setenv("OCR_BACKEND", configured)
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)

    assert ocr_backends.default_backend() == expected
    assert ocr_backends.resolve_backend(None) == expected
    assert ocr_backends.resolve_backend("auto") == expected


def test_explicit_environment_backend_still_overrides_platform_default(monkeypatch):
    monkeypatch.setenv("OCR_BACKEND", "paddle_server")
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)

    assert ocr_backends.default_backend() == "paddle_server"


def test_catalog_never_exposes_macos_vision(monkeypatch):
    def fake_find_spec(name):
        return object() if name in {"paddle", "paddleocr"} else None

    monkeypatch.setattr(ocr_backends.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(ocr_backends, "paddle_v6_supported", lambda: True)

    catalog = {item["id"]: item for item in ocr_backends.backend_catalog()}

    assert "vision" not in catalog
    assert "hybrid" not in catalog
    assert catalog["paddle"]["available"] is True
    assert catalog["hybrid_server"]["route"] == {
        "page": ocr_backends.BACKEND_LABELS["paddle_server"],
        "date": ocr_backends.BACKEND_LABELS["paddle"],
        "seal": ocr_backends.BACKEND_LABELS["paddle"],
    }
    assert "人工复核" in catalog["paddle"]["safety_policy"]
    assert "跨模型" in catalog["hybrid_server"]["safety_policy"]
    assert catalog["paddle_server"]["max_page_side"] >= 640
    assert catalog["hybrid_server"]["max_page_side"] == catalog["paddle_server"]["max_page_side"]
    assert catalog["paddle"]["recommended"] is True
    assert "默认引擎" in catalog["paddle"]["usage_note"]
    assert "整批默认" in catalog["paddle_server"]["usage_note"]


def test_default_backend_raises_when_paddle_is_unavailable(monkeypatch):
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    catalog = [dict(item, available=item["id"] == "danzhengtong") for item in AVAILABLE]
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: catalog)
    with pytest.raises(RuntimeError, match="没有可用的 OCR 引擎"):
        ocr_backends.default_backend()


def test_unavailable_backend_is_rejected(monkeypatch):
    catalog = [dict(AVAILABLE[1], available=False, reason="需安装 paddlepaddle 与 paddleocr")]
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: catalog)
    with pytest.raises(RuntimeError, match="不可用"):
        ocr_backends.resolve_backend("paddle")


def test_server_backend_uses_large_model(monkeypatch):
    captured = {}

    def fake_recognize(_path, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_text", fake_recognize)
    assert ocr_backends.recognize_text("unused.jpg", backend="paddle_server") == []
    assert captured["model_variant"] == "server"


def test_hybrid_server_routes_page_to_paddle_server_and_details_to_paddle(monkeypatch):
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    assert ocr_backends.backend_route("hybrid_server") == {
        "page": "paddle_server", "date": "paddle", "seal": "paddle",
    }


def test_legacy_vision_and_hybrid_resolve_to_paddle(monkeypatch):
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    assert ocr_backends.resolve_backend("vision") == "paddle"
    assert ocr_backends.resolve_backend("hybrid") == "paddle"
    assert ocr_backends.backend_route("vision") == {
        "page": "paddle", "date": "paddle", "seal": "paddle",
    }
    assert ocr_backends.backend_route("hybrid") == {
        "page": "paddle", "date": "paddle", "seal": "paddle",
    }
    assert ocr_backends.backend_label("vision") == "macOS Vision（已下线）"


def test_hybrid_alias_routes_date_and_seal_to_paddle_mobile(monkeypatch):
    captured = []

    def fake_paddle(_path, **kwargs):
        captured.append(kwargs.get("model_variant"))
        return []

    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: AVAILABLE)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_text", fake_paddle)

    assert ocr_backends.recognize_text("unused.jpg", backend="hybrid", stage="date") == []
    assert ocr_backends.recognize_text("unused.jpg", backend="hybrid", stage="seal") == []
    assert captured == ["mobile", "mobile"]


# PP-OCRv6 ("multi_PP-OCRv6_small" in CnOCR terms).

V6_AVAILABLE = AVAILABLE


def test_ppocrv6_variant_uses_the_official_small_model_names():
    assert paddle_ocr.MODEL_VARIANTS["v6"] == ("PP-OCRv6_small_det", "PP-OCRv6_small_rec")


def test_ppocrv6_provider_id_matches_the_catalog():
    """A mismatch silently turns every v6 stage into an empty result."""
    catalog_ids = {item["id"] for item in ocr_backends.backend_catalog()}
    assert paddle_ocr.PADDLE_BACKENDS <= catalog_ids
    for variant, provider in paddle_ocr.VARIANT_PROVIDERS.items():
        assert paddle_ocr.provider_of(variant) == provider
        assert paddle_ocr.variant_of(provider) == variant
        assert paddle_ocr.is_paddle_backend(provider) is True
    assert paddle_ocr.variant_of("vision") is None
    assert paddle_ocr.is_paddle_backend("vision") is False


@pytest.mark.parametrize("variant, expected", [("mobile", "paddle"), ("server", "paddle_server"), ("v6", "paddle_v6")])
def test_every_paddle_variant_maps_to_a_distinct_provider(variant, expected):
    assert paddle_ocr.provider_of(variant) == expected


@pytest.mark.parametrize("variant, expected", [("mobile", "paddle"), ("server", "paddle_server"), ("v6", "paddle_v6")])
def test_ppocrv6_provider_allows_its_own_scope_only(variant, expected, monkeypatch):
    """recognition_scope gates by backend id, so the mapping must be exact."""
    seen = []

    def fake_allowed(provider):
        seen.append(provider)
        return False

    monkeypatch.setattr("receipt_ocr.recognition_scope.provider_allowed", fake_allowed)
    assert paddle_ocr.recognize_text("unused.jpg", model_variant=variant) == []
    assert seen == [expected]


def test_ppocrv6_routes_every_stage_to_itself(monkeypatch):
    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: V6_AVAILABLE)
    assert ocr_backends.backend_route("paddle_v6") == {
        "page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_v6",
    }


def test_ppocrv6_backend_selects_the_v6_model_variant(monkeypatch):
    captured = {}

    def fake_recognize(_path, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ocr_backends, "backend_catalog", lambda: V6_AVAILABLE)
    monkeypatch.setattr("receipt_ocr.paddle_ocr.recognize_text", fake_recognize)
    assert ocr_backends.recognize_text("unused.jpg", backend="paddle_v6") == []
    assert captured["model_variant"] == "v6"


def test_ppocrv6_is_hidden_until_paddleocr_3_7(monkeypatch):
    def fake_find_spec(name):
        return object() if name in {"paddle", "paddleocr"} else None

    monkeypatch.setattr(ocr_backends.importlib.util, "find_spec", fake_find_spec)

    monkeypatch.setattr(ocr_backends, "_distribution_version", lambda _name: "3.6.0")
    with_old_paddleocr = {item["id"]: item for item in ocr_backends.backend_catalog()}
    assert with_old_paddleocr["paddle"]["available"] is True
    assert with_old_paddleocr["paddle_v6"]["available"] is False
    assert "3.7.0" in with_old_paddleocr["paddle_v6"]["reason"]

    monkeypatch.setattr(ocr_backends, "_distribution_version", lambda _name: "3.7.0")
    assert {item["id"]: item for item in ocr_backends.backend_catalog()}["paddle_v6"]["available"] is True


def test_ppocrv6_label_never_claims_the_mobile_tier():
    """date_slot_evidence infers the model family from label text."""
    label = ocr_backends.BACKEND_LABELS["paddle_v6"]
    assert "mobile" not in label.lower()
    assert "v5" not in label.lower()
    assert paddle_ocr.is_lightweight_backend(label) is True


def test_lightweight_backend_accepts_ids_and_legacy_labels():
    assert paddle_ocr.is_lightweight_backend("paddle") is True
    assert paddle_ocr.is_lightweight_backend("paddle_v6") is True
    assert paddle_ocr.is_lightweight_backend("PaddleOCR PP-OCRv6 Small") is True
    assert paddle_ocr.is_lightweight_backend("PaddleOCR PP-OCRv5 Mobile") is True
    assert paddle_ocr.is_lightweight_backend("paddle_server") is False
    assert paddle_ocr.is_lightweight_backend("PaddleOCR PP-OCRv5 Server（大模型）") is False
    assert paddle_ocr.is_lightweight_backend("") is False
    assert paddle_ocr.is_lightweight_backend(None) is False


@pytest.mark.parametrize(
    "backend",
    ["paddle", "paddle_server", "paddle_v6"],
)
def test_single_paddle_safety_covers_every_paddle_tier(backend):
    """A lone Paddle model must never certify its own date/seal transforms."""
    from receipt_ocr.recognition_safety import _apply_single_paddle_safety

    date_check = {"confidence": 0.95, "reliable": True}
    seal_check = {"confidence": 0.95, "reliable": True}
    policy = _apply_single_paddle_safety(
        date_check, seal_check, {stage: backend for stage in ("page", "date", "seal")}
    )
    assert policy
    assert date_check["reliable"] is False
    assert seal_check["reliable"] is False
    assert date_check["confidence"] <= 0.68


def test_single_paddle_safety_stays_off_for_cross_model_routes():
    from receipt_ocr.recognition_safety import _apply_single_paddle_safety

    date_check = {"confidence": 0.95, "reliable": True}
    seal_check = {"confidence": 0.95, "reliable": True}
    assert _apply_single_paddle_safety(
        date_check, seal_check, {"page": "paddle_server", "date": "paddle", "seal": "paddle"}
    ) == ""
    assert date_check["reliable"] is True


def test_single_paddle_exact_date_match_is_reliable():
    from receipt_ocr.recognition_safety import _apply_single_paddle_safety

    date_check = {
        "required": "2026-02-06",
        "actual": "2026-02-06",
        "status": "匹配",
        "confidence": 0.95,
        "reliable": True,
    }
    seal_check = {"confidence": 0.95, "reliable": True}
    _apply_single_paddle_safety(
        date_check,
        seal_check,
        {stage: "paddle" for stage in ("page", "date", "seal")},
    )

    assert date_check["status"] == "匹配"
    assert date_check["reliable"] is True
    assert date_check["confidence"] == 0.68
    assert seal_check["reliable"] is False


@pytest.mark.parametrize("backend", ["paddle", "paddle_server", "paddle_v6"])
@pytest.mark.parametrize("text,status,reliable", [
    ("北京集中维修中心 业务章（3）", "匹配", True),
    ("北京集中维修中心", "部分匹配", False),
    ("北京集中维修中心业务章(2)", "不匹配", False),
    ("北京集中维修中心业务章(3)收", "不匹配", False),
    ("", "未识别", False),
])
def test_single_paddle_strict_seal_comparison(backend, text, status, reliable):
    from receipt_ocr.parsing_seals import compare_seal_text_strict
    from receipt_ocr.recognition_safety import _apply_single_paddle_safety

    seal_check = compare_seal_text_strict("北京集中维修中心业务章(3)", [text])
    _apply_single_paddle_safety(
        {},
        seal_check,
        {stage: backend for stage in ("page", "date", "seal")},
    )

    assert seal_check["status"] == status
    assert seal_check["reliable"] is reliable


def test_ppocrv6_is_selectable_in_a_recognition_plan():
    from receipt_ocr.recognition_config import validate_config
    from receipt_ocr.ocr_backends import backend_catalog

    if not {item["id"] for item in backend_catalog() if item["available"]} >= {"paddle_v6"}:
        pytest.skip("paddleocr>=3.7.0 is not installed")
    assert validate_config({"date": ["paddle_v6"]})["date"] == ["paddle_v6"]
    with pytest.raises(ValueError, match="不可用"):
        validate_config({"date": ["paddle_v7"]})
