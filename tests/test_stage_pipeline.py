"""Entry-point and routing regressions using actual document classification."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from receipt_ocr import (
    document_context,
    pipeline,
    recognition_config,
    stage_date,
    stage_fields,
    stage_products,
    stage_seal,
)
from receipt_ocr.analyzer import ReceiptAnalyzer
from receipt_ocr.document_context import DocumentContext, StageRequest
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.recognition_config import run_configured


def row(text, y=0.1):
    return TextObservation(text, 0.99, 0.1, y, 0.6, 0.02)


PAGES = {
    "receipt": [
        row("出库单 承运商 运单号 客户订单号 客户名称 要求到货"),
        row("签章要求：客户章", 0.48),
    ],
    "warehouse_authorization": [
        row("仓库货物接收委托书"),
        row("签章要求：客户章", 0.48),
    ],
    "unknown": [row("任意扫描文件"), row("签章要求：客户章", 0.48)],
    "product_continuation": [
        row("行号 产品类别 物料编号 等级 出库仓库 数量"),
        row("签章要求：客户章", 0.48),
    ],
}
FIELDS = {
    "运单号": "202609090001",
    "客户订单号": "12345",
    "客户名称": "测试客户",
    "要求到货": "2026-09-09",
    "签章要求": "客户章",
}


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(kind="receipt", pages=[], qr=0, dates=[], seals=[], api=[])

    def page(source, *, backend):
        state.pages.append((str(source), backend))
        return deepcopy(PAGES[state.kind])

    def qr(source):
        state.qr += 1
        return ""

    monkeypatch.setattr(document_context, "recognize_text", page)
    monkeypatch.setattr(document_context, "decode_qr", qr)
    monkeypatch.setattr(pipeline, "resolve_backend", lambda value: value or "paddle")
    monkeypatch.setattr(
        pipeline,
        "backend_route",
        lambda value: dict(page=value, date=value, seal=value),
    )
    monkeypatch.setattr(
        recognition_config,
        "backend_catalog",
        lambda: [dict(id=x, available=True) for x in ("paddle", "paddle_v6")],
    )
    monkeypatch.setattr(stage_fields, "parse_fields", lambda rows: deepcopy(FIELDS))
    monkeypatch.setattr(stage_fields, "enrich_fields", lambda fields, qr: fields)
    monkeypatch.setattr(stage_fields, "repair_contextual_fields", lambda fields: {})
    monkeypatch.setattr(stage_fields, "standardize_fixed_phrases", lambda fields: {})
    monkeypatch.setattr(
        stage_fields,
        "estimate_field_confidences",
        lambda fields, *args: {
            k: {"confidence": 0.99, "low_confidence": False} for k in fields
        },
    )
    monkeypatch.setattr(
        stage_fields, "_recover_signature_requirement", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        stage_products,
        "parse_product_table",
        lambda rows: {"rows": [], "status": "无商品"},
    )
    monkeypatch.setattr(
        stage_products, "_recover_missing_product_grades", lambda s, r, table, b: table
    )
    monkeypatch.setattr(stage_seal, "detect_seal_regions", lambda source: [])
    engine = ReceiptAnalyzer()

    def dates(*args, **kwargs):
        state.dates.append((args, kwargs))
        return [row("2026年9月9日", 0.58)], []

    def seals(*args, **kwargs):
        state.seals.append((args, kwargs))
        return ["客户章"], []

    def remote(source):
        state.api.append(source)
        return {"enabled": True, "ok": True, "response": {"data": {"text": "客户章"}}}

    engine._recognize_receipt_date = dates
    engine._recognize_local_seals = seals
    engine.seal_api = SimpleNamespace(
        enabled=True, recognize=remote, skipped=lambda: {"enabled": False, "ok": False}
    )
    return engine, state


@pytest.mark.parametrize("kind", PAGES)
def test_both_entries_route_same_document_and_preserve_evidence(harness, kind):
    engine, state = harness
    state.kind = kind
    direct = engine.analyze(
        "document.jpg", ocr_backend="paddle", seal_recognition_mode="local"
    )
    configured = run_configured(
        engine,
        "document.jpg",
        None,
        config={stage: ["paddle"] for stage in pipeline.STAGES},
        ocr_backend="paddle",
    )
    assert (
        direct["document_type"]["type"] == configured["document_type"]["type"] == kind
    )
    for key in (
        "fields",
        "field_metadata",
        "product_table",
        "date_check",
        "seal_check",
        "overall",
    ):
        assert direct[key] == configured[key], key
    assert len(state.pages) == 2  # One full-page OCR per run, not one per stage.
    if kind in {"unknown", "warehouse_authorization"}:
        assert state.dates == state.seals == state.api == []
        assert direct["overall"] == "需人工复核"
    if kind == "product_continuation":
        assert len(state.dates) == len(state.seals) == 2
        assert all(
            call[1]["allow_strict_date_without_requirement"] for call in state.dates
        )
        assert configured["overall"] == "需人工复核"
        assert any("合并复核" in reason for reason in configured["review_reasons"])


def test_configured_dispatch_never_calls_legacy_analyze(harness, monkeypatch):
    engine, state = harness
    monkeypatch.setattr(
        engine, "analyze", lambda *a, **kw: pytest.fail("legacy entry called")
    )
    result = run_configured(
        engine,
        "document.jpg",
        None,
        config={stage: ["paddle"] for stage in pipeline.STAGES},
    )
    assert result["recognition_status"] == {
        stage: "已执行" for stage in pipeline.STAGES
    }
    assert len(state.pages) == 1


@pytest.mark.parametrize("stage", pipeline.STAGES)
def test_partial_retry_only_executes_selected_stage(harness, stage):
    engine, state = harness
    state.kind = "product_continuation"
    result = run_configured(
        engine,
        "continuation.jpg",
        None,
        config={stage: ["paddle"]},
        previous_fields=FIELDS,
    )
    assert bool(state.dates) == (stage == "date")
    assert bool(state.seals) == (stage == "seal")
    assert state.qr == (1 if stage == "fields" else 0)
    assert result["overall"] == "需人工复核"
    assert result["fields"]["签章要求"] == FIELDS["签章要求"]
    assert FIELDS.get("商品明细原文") is None


@pytest.mark.parametrize("kind", ["warehouse_authorization", "unknown"])
def test_nonstandard_plan_never_prefetches_remote_seal(harness, kind):
    engine, state = harness
    state.kind = kind
    result = run_configured(
        engine,
        "other.jpg",
        None,
        config={"fields": ["paddle"], "seal": ["qingtong"]},
        ocr_backend="paddle",
    )
    assert state.api == []
    assert result["overall"] == "需人工复核"
    assert result["seal_check"]["recognized"] == ""


def test_remote_only_does_not_invent_document_classification(harness):
    engine, state = harness
    result = run_configured(
        engine, "seal.jpg", None, config={"seal": ["qingtong"]}, previous_fields=FIELDS
    )
    assert state.pages == [] and state.qr == 0
    assert len(state.api) == 1
    assert result["document_type"]["type"] == "unclassified"
    assert result["overall"] == "需人工复核"


def test_multiple_field_providers_share_qr_but_keep_independent_pages(harness):
    engine, state = harness
    run_configured(
        engine,
        "document.jpg",
        None,
        config={"fields": ["paddle_v6", "paddle"], "products": ["paddle_v6", "paddle"]},
    )
    assert [backend for _, backend in state.pages] == ["paddle_v6", "paddle"]
    assert state.qr == 1


@pytest.mark.parametrize("first_rows", [[], PAGES["unknown"]])
def test_selected_provider_recovers_unknown_routing_before_date_and_remote_seal(
    harness, monkeypatch, first_rows
):
    engine, state = harness

    def page(source, *, backend):
        state.pages.append((str(source), backend))
        return deepcopy(first_rows if backend == "paddle_v6" else PAGES["receipt"])

    monkeypatch.setattr(document_context, "recognize_text", page)
    monkeypatch.setattr(
        recognition_config, "backend_route",
        lambda _: dict(page="paddle", date="paddle", seal="paddle"),
    )
    result = run_configured(
        engine, "document.jpg", None,
        config={"fields": ["paddle_v6", "paddle"], "date": ["paddle"], "seal": ["qingtong"]},
    )

    assert result["document_type"]["type"] == "receipt"
    assert result["ocr_stage_backends"]["page"]["id"] == "paddle"
    assert "出库单" in result["raw_text"]
    assert [backend for _, backend in state.pages] == ["paddle_v6", "paddle"]
    assert len(state.dates) == len(state.api) == 1


def test_unknown_routing_cannot_use_unselected_provider(harness):
    from receipt_ocr.recognition_scope import provider_scope

    _, state = harness
    state.kind = "unknown"
    context = DocumentContext("document.jpg")
    with provider_scope({"paddle_v6"}):
        context.page("paddle_v6")
        state.kind = "receipt"
        with pytest.raises(ValueError, match="未授权"):
            context.page("paddle")

    assert context.document_type["type"] == "unknown"
    assert [backend for _, backend in state.pages] == ["paddle_v6"]


def test_reliable_nonreceipt_routing_stays_fixed_with_another_provider(harness):
    _, state = harness
    state.kind = "warehouse_authorization"
    context = DocumentContext("document.jpg")
    context.page("paddle")
    state.kind = "receipt"
    context.page("paddle")

    assert context.document_type["type"] == "warehouse_authorization"
    assert context.primary_backend == "paddle"
    assert not context.allows_remote_seal


@pytest.mark.parametrize("observations", [1, 2])
def test_explicit_other_month_cannot_pass_date_stage(harness, observations):
    engine, _ = harness
    engine._recognize_receipt_date = lambda *a, **kw: (
        [TextObservation("6月11日", .95, .7, .6, .15, .04)] * observations, []
    )
    result = engine.run_stage(
        DocumentContext("document.jpg"), "date",
        StageRequest(
            dict(page="paddle", date="paddle", seal="paddle"),
            {**FIELDS, "要求到货": "2025-05-11", "制单日期": "2025-05-01"},
        ),
    )

    assert result["date_check"]["actual"] == "2025-06-11"
    assert result["date_check"]["status"] == "不匹配"
    if observations == 1:
        assert not result["date_check"]["reliable"]


def test_seal_reference_runs_before_verdict_with_original_filename(harness):
    engine, state = harness
    calls = []

    def match(result):
        assert "overall" not in result
        calls.append(result["filename"])
        return {
            "accepted": True,
            "reference_filename": "different.jpg",
            "confidence": 0.95,
        }

    result = engine.analyze(
        "uuid-upload.jpg",
        filename="original.jpg",
        ocr_backend="paddle",
        reference_matcher=SimpleNamespace(match=match),
    )
    assert calls == ["original.jpg"]
    assert result["seal_check"]["visual_reference_match"]["accepted"]
    assert "印章内容无法可靠判断" not in result["review_reasons"]
    assert result["final_result"] == result["overall"]


def test_stage_result_has_no_final_verdict_or_unselected_stage(harness):
    engine, _ = harness
    context = DocumentContext("document.jpg")
    result = engine.run_stage(
        context,
        "fields",
        StageRequest(dict(page="paddle", date="paddle", seal="paddle")),
    )
    assert "fields" in result and "stage_review_reasons" in result
    assert not {"overall", "date_check", "seal_check", "product_table"} & result.keys()


def test_direct_stage_context_reuses_page_without_outer_decorator(harness):
    engine, state = harness
    context = DocumentContext("document.jpg")
    request = StageRequest(dict(page="paddle", date="paddle", seal="paddle"))
    engine.run_stage(context, "fields", request)
    engine.run_stage(context, "products", request)
    assert len(state.pages) == 1
    copy = context.page("paddle")
    copy.clear()
    assert context.page("paddle")


@pytest.mark.parametrize("low_field, expected_reasons", [
    ("客户名称", ["关键字段低置信度：客户名称", "存在低置信度字段"]),
    ("收货地址", []),
])
def test_low_confidence_field_uses_same_review_policy_for_both_entries(
    harness, monkeypatch, low_field, expected_reasons
):
    engine, state = harness
    monkeypatch.setattr(
        stage_fields,
        "estimate_field_confidences",
        lambda *a: {
            **{k: {"confidence": 0.99, "low_confidence": False} for k in FIELDS},
            low_field: {"confidence": 0.2, "low_confidence": True},
        },
    )
    monkeypatch.setattr(
        stage_date,
        "execute",
        lambda *a: {
            "date_check": {"status": "匹配", "actual": "2026-09-09", "reliable": True},
            "stage_review_reasons": [],
        },
    )
    monkeypatch.setattr(
        stage_seal,
        "execute",
        lambda *a: {
            "seal_check": {"status": "匹配", "recognized": "客户章", "reliable": True},
            "stage_review_reasons": [],
        },
    )
    direct = engine.analyze("document.jpg", ocr_backend="paddle")
    configured = run_configured(
        engine,
        "document.jpg",
        None,
        config={stage: ["paddle"] for stage in pipeline.STAGES},
    )
    for result in (direct, configured):
        assert result["overall"] == ("需人工复核" if expected_reasons else "通过")
        assert result["review_reasons"] == expected_reasons
