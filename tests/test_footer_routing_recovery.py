"""Footer routing must survive obscured labels without inventing receipt data."""

from dataclasses import replace
from types import SimpleNamespace
from PIL import Image

import pytest

from receipt_ocr.application.assembly import _recognition_status, finish_result
from receipt_ocr.application.context import DocumentContext
from receipt_ocr.application.requests import StageRequest
from receipt_ocr.domain.documents.layout import signature_footer_evidence
from receipt_ocr.domain.documents.pagination import merge_paginated_results
from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.stages import STAGES
from receipt_ocr.stages import date, fields, seal


def row(text, x=.6, y=.49, confidence=.98):
    return TextObservation(text, confidence, x, y, .08, .014)


def table_rows():
    # Independent observations from 7272433468: both first footer labels
    # were missing, while these quantity/signing-table labels survived.
    return [row("实收数量：", .576, .489), row("拒收数量：", .765, .489),
            row("仓库接收人", .593, .537), row("盖章", .749, .533)]


def make_context(tmp_path, observations):
    Image.new('RGB', (1000, 1000), 'white').save(tmp_path / 'receipt.jpg')
    context = DocumentContext(tmp_path / "receipt.jpg", text_recognizer=SimpleNamespace(
        recognize=lambda *a, **kw: observations,
    ))
    context.document_type = {"type": "receipt", "reliable": True, "confidence": .99}
    context.primary_rows = observations
    context._qr = ""
    context.page("paddle_v6")
    return context


def request():
    return StageRequest(route={k: "paddle_v6" for k in ("page", "date", "seal")},
                        fields={"要求到货": "2025-02-10", "签章要求": "太原市伊加壹电子服务总汇"})


def test_quantity_and_signing_geometry_recovers_obscured_footer():
    evidence = signature_footer_evidence(table_rows())
    assert evidence["present"] is True
    assert evidence["method"] == "quantity_and_signing_table"
    assert .46 < evidence["anchor"].y < .49


@pytest.mark.parametrize("observations", [
    [], [row("日期")], [row("盖章")], [row("日期"), row("盖章")],
    table_rows()[:2],
    [row("实收数量 拒收数量 仓库接收人 盖章")],
    [row("实收数量 拒收数量"), row("仓库接收人", y=.54)],
    [row("实收数量", y=.40), row("拒收数量", y=.65), row("盖章", y=.85)],
    [row("实收数量", y=.49), row("拒收数量", y=.49), row("盖章", x=.05, y=.54)],
    [replace(r, confidence=.5) for r in table_rows()],
    [replace(r, y=.2) for r in table_rows()],
])
def test_isolated_or_incoherent_labels_do_not_enable_footer(observations):
    assert signature_footer_evidence(observations)["present"] is False


def test_existing_explicit_and_note_footer_routes_are_preserved():
    assert signature_footer_evidence([row("签章要求：测试收货章", y=.47)])["present"]
    assert signature_footer_evidence([
        row("签收说明", y=.48), row("仓库接收人", y=.53), row("盖章", y=.53),
    ])["present"]


def test_reliable_document_type_does_not_freeze_missing_footer(tmp_path):
    context = make_context(tmp_path, [])
    assert not context.has_footer
    assert context.routing_issues[0]["code"] == "footer_unconfirmed"
    context.text_recognizer = SimpleNamespace(recognize=lambda *a, **kw: table_rows())
    context.page("other_backend")
    assert context.has_footer
    assert context.allows_remote_seal
    assert context.routing_issues == []
    context.refresh_footer([])
    assert context.has_footer  # A weaker later read must not erase evidence.


def test_clean_page_requirement_confirms_footer_before_downstream_stages(tmp_path):
    recovered = row("签章要求：太原市伊加壹电子服务总汇", y=.47, confidence=.991)
    observations = [recovered, row("实收数量", y=.49), row("仓库接收人", y=.54)]
    context = make_context(tmp_path, observations)
    result = fields.recognize(context, request())
    assert result.fields["签章要求"] == "太原市伊加壹电子服务总汇"
    assert context.has_footer
    assert context.footer_anchor == recovered
    assert context.routing_issues == []
    assert context.evidence()["signature_footer"]["method"] == "printed_requirement"


@pytest.mark.parametrize("recovered", [
    row("太原市伊加壹电子服务总汇", y=.47),
    row("签章要求：太原市伊加壹电子服务总汇", y=.47, confidence=.6),
    row("签章要求：太原市伊加壹电子服务总汇", y=.2),
])
def test_weak_recovered_requirement_is_not_footer_evidence(recovered):
    observations = [row("实收数量", y=.49), row("仓库接收人", y=.54)]
    assert not signature_footer_evidence(observations, recovered)["present"]


@pytest.mark.parametrize("recovered", [False, True])
def test_date_and_seal_use_confirmed_footer_anchor(tmp_path, monkeypatch, recovered):
    observations = [row("实收数量", y=.49), row("仓库接收人", y=.54)] if recovered else table_rows()
    context = make_context(tmp_path, observations)
    if recovered:
        context.confirm_requirement_footer({"footer_row": row(
            "签章要求：太原市伊加壹电子服务总汇", y=.47,
        ).to_dict()})
    calls = []

    def recognize_date(*args, **kwargs):
        calls.append(("date", args[1]))
        # A deliberately different actual date proves the required date does
        # not silently become the receipt date after routing is recovered.
        return [row("2025年2月9日", .83, .55)], [{"decision_rows": [
            row("2025年2月9日", .83, .55).to_dict(),
        ]}]

    def recognize_seals(*args, **kwargs):
        calls.append(("seal", kwargs["footer_anchor_y"]))
        return ["太原市伊加壹电子服务总汇"], []

    monkeypatch.setattr(seal, "detect_seal_regions", lambda *a: [])
    date_result = date.recognize(context, request(), recognize_date)
    seal_result = seal.recognize(context, request(), recognize_seals,
                                 SimpleNamespace(skipped=lambda: {"enabled": False}))
    assert calls == [("date", context.footer_anchor.y), ("seal", context.footer_anchor.y)]
    assert date_result.check["actual"] == "2025-02-09"
    assert seal_result.check["recognized"] == "太原市伊加壹电子服务总汇"


def test_genuine_missing_footer_skips_both_stages_and_reports_it(tmp_path):
    context = make_context(tmp_path, [row("日期"), row("盖章")])

    def forbidden(*args, **kwargs):
        pytest.fail("Unconfirmed footer must not call date/seal recognition")

    api = SimpleNamespace(skipped=lambda: {"enabled": False}, recognize=forbidden)
    checks = {"date": date.recognize(context, request(), forbidden).check,
              "seal": seal.recognize(context, request(), forbidden, api).check}
    for stage, check in checks.items():
        assert check["execution_status"] == "skipped"
        assert not check.get("actual") and not check.get("recognized")
        variants = [{"method": "paddle_v6", "result": {f"{stage}_check": check}}]
        assert _recognition_status(stage, ["paddle_v6"], variants) == "已跳过（页脚尚未确认）"


def test_skipped_variants_do_not_claim_a_backend_executed(tmp_path, monkeypatch):
    from receipt_ocr.application import assembly
    monkeypatch.setattr(assembly, "complete_result", lambda *a, **kw: None)
    context = make_context(tmp_path, [])
    config = {stage: ["paddle_v6"] if stage in {"date", "seal"} else [] for stage in STAGES}
    results = {stage: [] for stage in STAGES}
    for stage in ("date", "seal"):
        results[stage] = [{"method": "paddle_v6", "result": {
            f"{stage}_check": {"execution_status": "skipped"},
        }}]
    result = finish_result({}, context, config, results, [], [], [], [], None,
                           context.source, None, {})
    assert result["ocr_stage_backends"]["date"]["id"] == "skipped"
    assert result["ocr_stage_backends"]["seal"]["id"] == "skipped"
    assert result["recognition_status"]["date"] == "已跳过（页脚尚未确认）"


def test_selected_failed_and_mixed_execution_statuses_remain_distinct():
    skipped = {"method": "a", "result": {"date_check": {"execution_status": "skipped"}}}
    executed = {"method": "b", "result": {"date_check": {"actual": "2025-02-10"}}}
    assert _recognition_status("date", [], []) == "未执行"
    assert _recognition_status("date", ["a"], [{"error": "failed"}]) == "识别失败"
    assert _recognition_status("date", ["a", "b"], [skipped, executed]) == "已执行"


@pytest.mark.parametrize("continuation_has_footer", [False, True])
def test_page_association_resolves_footer_issue_only_with_footer_evidence(continuation_has_footer):
    cover = {
        "id": 1, "task_id": "task", "filename": "receipt.jpg",
        "document_type": {"type": "receipt"}, "fields": {"客户订单号": "1"},
        "review_issues": [{"code": "footer_unconfirmed", "scope": "document",
                           "message": "签收页脚尚未确认", "blocking": True, "provider": ""}],
    }
    continuation = {
        "id": 2, "task_id": "task", "filename": "receipt_01.jpg",
        "document_type": {"type": "product_continuation"},
        "fields": {"签章要求": "客户收货章"} if continuation_has_footer else {},
    }
    result = merge_paginated_results([cover, continuation])[0]
    codes = {item["code"] for item in result["review_issues"]}
    assert ("footer_unconfirmed" not in codes) == continuation_has_footer
