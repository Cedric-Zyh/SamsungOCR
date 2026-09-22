"""Policy and plumbing for the bounded second-model seal audit.

The audit used to hard-code ``paddle_server``.  Under a recognition plan the
request scope is exactly the backend the user selected, so every audit call was
denied and returned ``[]``: the whole branch produced nothing while still
looking as though it had run.  These tests lock the resolved-provider contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from receipt_ocr import paddle_ocr, seal_crop_audit, seal_audit_policy
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.recognition_scope import provider_scope
from receipt_ocr.seal_audit_policy import (
    BAND_READER_BACKEND,
    DEFAULT_AUDIT_BACKEND,
    SKIP_DISABLED,
    SKIP_SAME_MODEL,
    SKIP_UNAUTHORISED,
    describe_seal_audit,
    resolve_seal_audit,
    seal_audit_mode,
    seal_audit_providers_for_plan,
    variant_for_audit_backend,
)
from receipt_ocr.seal_crop_types import (
    SealAuditEvidence,
    SealAuditRoute,
    SealCropRequest,
    SealEvidenceCollection,
)


@pytest.fixture(autouse=True)
def _default_mode(monkeypatch):
    monkeypatch.delenv("SEAL_AUDIT_MODE", raising=False)


@pytest.fixture(autouse=True)
def _pristine_engine_state():
    """Keep the module-level predictor caches from leaking between tests."""
    pipelines, recognizers = dict(paddle_ocr._PIPELINES), dict(paddle_ocr._LINE_RECOGNIZERS)
    state = dict(paddle_ocr._ENGINE_STATE)
    yield
    paddle_ocr._PIPELINES.clear()
    paddle_ocr._PIPELINES.update(pipelines)
    paddle_ocr._LINE_RECOGNIZERS.clear()
    paddle_ocr._LINE_RECOGNIZERS.update(recognizers)
    paddle_ocr._ENGINE_STATE.update(state)


def _row(text: str, confidence: float = 0.9) -> TextObservation:
    return TextObservation(text, confidence, 0.0, 0.0, 1.0, 1.0)


def _request(tmp_path: Path, backend: str) -> SealCropRequest:
    source = tmp_path / "receipt.jpg"
    source.write_bytes(b"receipt")
    return SealCropRequest(
        source=source,
        rows=[],
        artifact_dir=tmp_path / "artifacts",
        artifact_url_prefix="/files/artifacts/x",
        ocr_backend=backend,
        secondary_ocr_backend=None,
        requirement="深圳市星睿奇光电有限公司仓储部收货章",
        footer_anchor_y=0.47,
    )


# --------------------------------------------------------------------------- #
# SEAL_AUDIT_MODE parsing
# --------------------------------------------------------------------------- #

def test_unknown_or_missing_mode_falls_back_to_auto(monkeypatch):
    assert seal_audit_mode() == "auto"
    monkeypatch.setenv("SEAL_AUDIT_MODE", "  SERVER ")
    assert seal_audit_mode() == "server"
    monkeypatch.setenv("SEAL_AUDIT_MODE", "nonsense")
    assert seal_audit_mode() == "auto"


def test_every_advertised_mode_is_accepted(monkeypatch):
    for mode in seal_audit_policy.SEAL_AUDIT_MODES:
        monkeypatch.setenv("SEAL_AUDIT_MODE", mode)
        assert seal_audit_mode() == mode
        assert describe_seal_audit()["mode"] == mode


# --------------------------------------------------------------------------- #
# Provider resolution
# --------------------------------------------------------------------------- #

def test_off_skips_everywhere_with_its_own_reason(monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "off")
    for backend, allowed in (
        ("paddle_v6", None),
        ("paddle_v6", frozenset({"paddle_v6"})),
        ("paddle_server", frozenset({"paddle_server"})),
    ):
        plan = resolve_seal_audit(backend, allowed=allowed)
        assert plan.runs is False
        assert plan.skip_reason == SKIP_DISABLED
        assert plan.authorise == frozenset()


def test_auto_keeps_the_unrestricted_run_auditing_server():
    plan = resolve_seal_audit("paddle_v6", allowed=None)
    assert plan.runs is True
    assert plan.backend == DEFAULT_AUDIT_BACKEND
    assert plan.band_backend == BAND_READER_BACKEND
    assert plan.independent is True
    assert plan.band_pair is True


def test_auto_skips_when_the_plan_never_allowed_the_server_model():
    """The regression this whole module exists for."""
    plan = resolve_seal_audit("paddle_v6", allowed=frozenset({"paddle_v6"}))
    assert plan.runs is False
    assert plan.skip_reason == SKIP_UNAUTHORISED
    assert plan.authorise == frozenset()


def test_auto_runs_when_the_plan_did_allow_the_server_model():
    plan = resolve_seal_audit(
        "paddle_v6", allowed=frozenset({"paddle_v6", "paddle_server"})
    )
    assert plan.runs is True
    assert plan.backend == DEFAULT_AUDIT_BACKEND
    assert plan.independent is True


def test_auditing_with_the_stage_s_own_model_is_refused(monkeypatch):
    for mode in ("auto", "server"):
        monkeypatch.setenv("SEAL_AUDIT_MODE", mode)
        plan = resolve_seal_audit("paddle_server", allowed=None)
        assert plan.runs is False
        assert plan.skip_reason == SKIP_SAME_MODEL.format(
            backend=DEFAULT_AUDIT_BACKEND
        )


def test_server_mode_audits_even_when_the_plan_did_not_allow_it(monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "server")
    plan = resolve_seal_audit("paddle_v6", allowed=frozenset({"paddle_v6"}))
    assert plan.runs is True
    assert plan.backend == DEFAULT_AUDIT_BACKEND
    assert plan.band_backend == BAND_READER_BACKEND
    assert plan.authorise == frozenset(
        {DEFAULT_AUDIT_BACKEND, BAND_READER_BACKEND}
    )
    assert plan.independent is True
    assert plan.band_pair is True


def test_local_mode_reuses_a_light_tier_without_loading_server(monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "local")
    plan = resolve_seal_audit("danzhengtong", allowed=frozenset({"danzhengtong"}))
    assert plan.runs is True
    assert plan.backend == "paddle"
    assert plan.band_backend is None
    assert plan.independent is True
    # No second tier is authorised, so no cross-model suffix can be claimed.
    assert plan.band_pair is False
    assert plan.authorise == frozenset({"paddle"})


def test_local_mode_declines_when_it_would_only_repeat_the_same_model(monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "local")
    for backend in ("paddle", "paddle_v6", "paddle_server"):
        plan = resolve_seal_audit(backend, allowed=None)
        assert plan.runs is False
        assert plan.skip_reason == SKIP_SAME_MODEL.format(backend=backend)


def test_plan_note_distinguishes_independent_reads():
    assert "跨模型佐证" in resolve_seal_audit("paddle_v6", allowed=None).note
    with_scope = resolve_seal_audit("paddle_v6", allowed=frozenset({"paddle_v6"}))
    assert with_scope.note == with_scope.skip_reason


# --------------------------------------------------------------------------- #
# Scope widening
# --------------------------------------------------------------------------- #

def test_auto_never_widens_the_scope():
    for method in ("paddle_v6", "paddle", "paddle_v6"):
        assert seal_audit_providers_for_plan({"seal": [method]}) == frozenset()


def test_server_mode_widens_the_scope_for_every_local_seal_backend(monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "server")
    expected = frozenset({DEFAULT_AUDIT_BACKEND, BAND_READER_BACKEND})
    assert seal_audit_providers_for_plan({"seal": ["paddle_v6"]}) == expected
    assert seal_audit_providers_for_plan({"seal": ["paddle"]}) == expected
    assert (
        seal_audit_providers_for_plan({"seal": ["paddle_v6", "qingtong"]}) == expected
    )


def test_remote_only_seal_stages_never_widen_the_scope(monkeypatch):
    for mode in ("auto", "server", "local", "off"):
        monkeypatch.setenv("SEAL_AUDIT_MODE", mode)
        assert seal_audit_providers_for_plan({"seal": ["qingtong"]}) == frozenset()
        assert seal_audit_providers_for_plan({"seal": ["danzhengtong"]}) == frozenset()
        assert seal_audit_providers_for_plan({"seal": []}) == frozenset()
        assert seal_audit_providers_for_plan(None) == frozenset()


def test_local_mode_widens_only_the_light_tier(monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "local")
    # A non-Paddle seal provider falls back to the local Paddle tier, which is
    # the only widening local mode performs (never the Server model).
    plan = resolve_seal_audit("danzhengtong", allowed=frozenset({"danzhengtong"}))
    assert plan.backend == "paddle"
    assert plan.authorise == frozenset({"paddle"})
    # A Paddle tier would repeat its own model, so it is skipped.
    assert seal_audit_providers_for_plan({"seal": ["paddle_v6"]}) == frozenset()


def test_variant_for_audit_backend_maps_every_paddle_tier():
    assert variant_for_audit_backend("paddle_server") == "server"
    assert variant_for_audit_backend("paddle") == "mobile"
    assert variant_for_audit_backend("paddle_v6") == "v6"
    assert variant_for_audit_backend(None) == "mobile"


# --------------------------------------------------------------------------- #
# The audit reads through the resolved provider, not a literal
# --------------------------------------------------------------------------- #

def test_audit_reads_through_the_resolved_backend(tmp_path, monkeypatch):
    seen: list[str] = []

    def fake_recognize(path, *, backend=None, **_kwargs):
        seen.append(backend)
        return [_row(Path(path).stem)]

    monkeypatch.setattr(seal_crop_audit, "recognize_text", fake_recognize)
    for name in ("seal-0-color.png", "seal-0-unwrapped.png"):
        (tmp_path / name).write_bytes(b"crop")
    audit = SealAuditEvidence(
        audit_paths=[
            ("保留章色白底图", tmp_path / "seal-0-color.png"),
            ("圆章/矩形校正图", tmp_path / "seal-0-unwrapped.png"),
        ]
    )

    seal_crop_audit._read_server_audit_evidence(
        audit, SealAuditRoute(audit_backend="paddle_v6")
    )

    assert seen == ["paddle_v6", "paddle_v6"]
    assert audit.audit_texts == ["seal-0-color", "seal-0-unwrapped"]
    assert sorted(audit.audit_variant_texts) == ["保留章色白底图", "圆章/矩形校正图"]
    assert audit.audit_variant_texts["保留章色白底图"] == ["seal-0-color"]


def test_audit_line_labels_use_the_resolved_variant(tmp_path, monkeypatch):
    seen: list[str] = []

    def fake_line(_path, *, model_variant):
        seen.append(model_variant)
        return [_row("8421888")]

    monkeypatch.setattr(paddle_ocr, "recognize_line", fake_line)
    crop = tmp_path / "seal-0-code-line.png"
    crop.write_bytes(b"crop")
    audit = SealAuditEvidence(audit_paths=[("矩形编号章数字行", crop)])

    seal_crop_audit._read_server_audit_evidence(
        audit, SealAuditRoute(audit_backend="paddle")
    )

    assert seen == ["mobile"]
    assert audit.audit_texts == ["8421888"]


def test_audit_does_nothing_without_a_resolved_backend(tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("audit ran without a resolved backend")

    monkeypatch.setattr(seal_crop_audit, "recognize_text", explode)
    crop = tmp_path / "seal-0-color.png"
    crop.write_bytes(b"crop")
    audit = SealAuditEvidence(audit_paths=[("保留章色白底图", crop)])

    seal_crop_audit._read_server_audit_evidence(
        audit, SealAuditRoute(audit_backend=None)
    )

    assert audit.audit_texts == []
    assert audit.audit_variant_texts == {}


# --------------------------------------------------------------------------- #
# The skip is recorded on the artifact
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "mode,allowed,expected",
    [
        ("off", frozenset({"paddle_v6"}), SKIP_DISABLED),
        ("auto", frozenset({"paddle_v6"}), SKIP_UNAUTHORISED),
    ],
)
def test_wanted_audit_records_why_it_was_skipped(
    tmp_path, monkeypatch, mode, allowed, expected
):
    monkeypatch.setenv("SEAL_AUDIT_MODE", mode)
    request = _request(tmp_path, "paddle_v6")
    collection = SealEvidenceCollection(artifacts=[{"index": 0}])
    route = SealAuditRoute(should_server_audit=True)

    from receipt_ocr.seal_crops import _apply_seal_audit_plan

    with provider_scope(allowed):
        _apply_seal_audit_plan(request, collection, route)

    assert route.audit_backend is None
    assert route.audit_skip_reason == expected
    assert collection.artifacts[0]["server_audit_skipped_reason"] == expected
    assert collection.artifacts[0]["server_audit_mode"] == mode


def test_unwanted_audit_leaves_artifacts_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAL_AUDIT_MODE", "auto")
    request = _request(tmp_path, "paddle_v6")
    collection = SealEvidenceCollection(artifacts=[{"index": 0}])
    route = SealAuditRoute(should_server_audit=False)

    from receipt_ocr.seal_crops import _apply_seal_audit_plan

    with provider_scope(frozenset({"paddle_v6"})):
        _apply_seal_audit_plan(request, collection, route)

    assert collection.artifacts[0] == {"index": 0}


def test_resolved_provider_survives_the_request_scope(tmp_path, monkeypatch):
    """End-to-end shape: the resolved backend must pass the scope gate."""
    monkeypatch.setenv("SEAL_AUDIT_MODE", "server")
    request = _request(tmp_path, "paddle_v6")
    collection = SealEvidenceCollection(artifacts=[{"index": 0}])
    route = SealAuditRoute(should_server_audit=True)

    from receipt_ocr.seal_crops import _apply_seal_audit_plan
    from receipt_ocr.seal_audit_policy import seal_audit_providers_for_plan

    scope = frozenset({"paddle_v6"}) | seal_audit_providers_for_plan(
        {"seal": ["paddle_v6"]}
    )
    with provider_scope(scope):
        _apply_seal_audit_plan(request, collection, route)
        from receipt_ocr.recognition_scope import provider_allowed

        assert provider_allowed(route.audit_backend) is True
        assert provider_allowed(route.audit_band_backend) is True

    assert route.audit_backend == DEFAULT_AUDIT_BACKEND
    assert collection.artifacts[0] == {"index": 0}


# --------------------------------------------------------------------------- #
# ONNX Runtime engine selection
# --------------------------------------------------------------------------- #

def test_engine_defaults_to_onnxruntime_when_installed(monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_ENGINE", raising=False)
    pytest.importorskip("onnxruntime")
    assert paddle_ocr.paddle_engine() == paddle_ocr.DEFAULT_ENGINE


@pytest.mark.parametrize("value", ["off", "paddle", "cpu", "", "0", "false", "none"])
def test_engine_can_be_switched_back_to_the_default_kernels(monkeypatch, value):
    monkeypatch.setenv("PADDLE_OCR_ENGINE", value)
    assert paddle_ocr.paddle_engine() is None
    assert paddle_ocr._engine_kwargs() == {}


def test_engine_falls_back_when_onnxruntime_is_unavailable(monkeypatch):
    monkeypatch.setenv("PADDLE_OCR_ENGINE", "onnxruntime")
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    real_import = __import__

    def blocked(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("onnxruntime is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    assert paddle_ocr.paddle_engine() is None


def test_engine_change_drops_cached_predictors(monkeypatch):
    pytest.importorskip("onnxruntime")
    monkeypatch.delenv("PADDLE_OCR_ENGINE", raising=False)
    assert paddle_ocr._engine_kwargs() == {"engine": paddle_ocr.DEFAULT_ENGINE}
    paddle_ocr._PIPELINES["mobile"] = object()
    paddle_ocr._LINE_RECOGNIZERS["mobile"] = object()
    monkeypatch.setenv("PADDLE_OCR_ENGINE", "off")
    assert paddle_ocr._engine_kwargs() == {}
    assert paddle_ocr._PIPELINES == {}
    assert paddle_ocr._LINE_RECOGNIZERS == {}


def test_stable_engine_keeps_cached_predictors(monkeypatch):
    monkeypatch.setenv("PADDLE_OCR_ENGINE", "off")
    assert paddle_ocr._engine_kwargs() == {}
    sentinel = object()
    paddle_ocr._PIPELINES["mobile"] = sentinel
    assert paddle_ocr._engine_kwargs() == {}
    assert paddle_ocr._PIPELINES["mobile"] is sentinel


# --------------------------------------------------------------------------- #
# The settings surface
# --------------------------------------------------------------------------- #

def test_backend_catalog_endpoint_surfaces_engine_and_audit(monkeypatch):
    import app as web

    monkeypatch.setenv("SEAL_AUDIT_MODE", "local")
    with web.app.test_request_context("/api/ocr-backends"):
        payload = web.ocr_backends().get_json()

    assert payload["engine"] == {
        "id": paddle_ocr.paddle_engine(),
        "env": "PADDLE_OCR_ENGINE",
    }
    assert payload["seal_audit"]["mode"] == "local"
    assert payload["seal_audit"]["modes"] == list(seal_audit_policy.SEAL_AUDIT_MODES)
    assert payload["seal_audit"]["description"]
    assert payload["seal_audit"]["independent"] is False
