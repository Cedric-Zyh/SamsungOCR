"""Only the application owns remote waiting and retry decisions."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from receipt_ocr.application.qingtong_dispatch import QingTongDispatch
from receipt_ocr.application.requests import StageRequest


def test_failed_prefetch_is_settled_once_and_never_repeats_an_upload():
    future = Mock()
    future.result.side_effect = RuntimeError("saved transport failure")
    client = Mock()
    dispatch = QingTongDispatch(client, "receipt.png", future=future)
    context = SimpleNamespace(allows_remote_seal=True)
    local = dispatch.bind(context, StageRequest({}, {"签章要求": "客户章"}))
    remote = dispatch.bind(context, StageRequest({}, seal_mode="qingtong_only"))
    assert local.seal_evidence is remote.seal_evidence
    assert local.seal_evidence.response is None
    with pytest.raises(RuntimeError, match="saved transport failure"):
        remote.seal_evidence.require_response()
    future.result.assert_called_once()
    client.recognize.assert_not_called()


def test_evidence_consumers_receive_independent_response_copies():
    future = Future()
    response = {"ok": True, "response": {"data": [{"text": "客户章"}]}}
    future.set_result(response)
    dispatch = QingTongDispatch(Mock(), "receipt.png", future=future)
    context = SimpleNamespace(allows_remote_seal=True)
    local = dispatch.bind(context, StageRequest({}, {"签章要求": "客户章"}))
    first = local.seal_evidence.require_response()
    first["response"]["data"][0]["text"] = "display edit"
    remote = dispatch.bind(context, StageRequest({}, seal_mode="qingtong_only"))
    assert remote.seal_evidence.require_response() == response


def test_blank_local_requirement_does_not_wait_for_selected_remote_work():
    future, client = Mock(), Mock()
    request = StageRequest({}, {"签章要求": ""})
    assert QingTongDispatch(client, "receipt.png", future=future).bind(Mock(), request) is request
    future.result.assert_not_called()
    client.recognize.assert_not_called()


def test_legacy_combined_entry_routes_before_starting_remote_request():
    from receipt_ocr.application.stage_runner import recognize_stage

    context = SimpleNamespace(source="receipt.png", document_type={"type": "unclassified"},
                              allows_remote_seal=True, has_footer=False)

    def route(backend):
        context.document_type = {"type": "warehouse_authorization"}
        context.allows_remote_seal = False
        return []

    context.page = Mock(side_effect=route)
    client, reader = Mock(), Mock()
    analyzer = SimpleNamespace(seal_api=client, _recognize_local_seals=reader)
    request = StageRequest(dict.fromkeys(("page", "date", "seal"), "paddle_v6"),
                           {"签章要求": "客户章"}, seal_mode="qingtong")
    result = recognize_stage(analyzer, context, "seal", request)
    context.page.assert_called_once_with("paddle_v6")
    client.recognize.assert_not_called()
    reader.assert_not_called()
    assert result.check["backend"] == "未执行（文档类型分流）"
