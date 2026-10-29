"""Chosen printed values must drive downstream checks and review evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from receipt_ocr.application.assembly import assemble_result
from receipt_ocr.application.plans import _recognize_stages
from receipt_ocr.domain.issues import issue
from receipt_ocr.domain.stages import STAGES


def context(tmp_path):
    document = {"type": "receipt", "reliable": True, "reasons": []}
    return SimpleNamespace(
        source=tmp_path / "receipt.jpg", primary_backend="paddle_v6", has_footer=True,
        document_type=document, allows_remote_seal=False, page=lambda backend: [],
        routing_issues=[], routing_reasons=[], evidence=lambda: {"document_type": deepcopy(document)},
    )


def config():
    return {**{stage: [] for stage in STAGES}, "acceptance": {"low_confidence_mode": "check"}}


@pytest.mark.parametrize("required", ["2025-01-04", "２０２５／１／４"])
def test_downstream_date_and_seal_use_the_same_per_field_selection_as_display(tmp_path, required):
    local = {"fields": {"客户名称": "测试客户", "客户订单号": "123", "要求到货": "", "签章要求": ""}}
    remote = {"fields": {"客户名称": "", "要求到货": required, "签章要求": "测试收货章"}}
    original = deepcopy((local, remote))
    received = {}

    def run_stage(ctx, stage, request):
        if stage == "fields":
            return local if request.route["page"] == "paddle_v6" else remote
        received[stage] = deepcopy(request.fields)
        if stage == "date":
            return {"date_check": {"required": request.fields["要求到货"], "actual": "2025-01-04",
                                    "status": "匹配", "reliable": True}}
        return {"seal_check": {"requirement": request.fields["签章要求"], "recognized": "测试收货章",
                                "status": "匹配", "reliable": True}}

    plan = config()
    plan.update(fields=["paddle_v6", "paddle_v6_server"], date=["paddle_v6"], seal=["paddle_v6"])
    ctx = context(tmp_path)
    analyzer = SimpleNamespace(run_stage=run_stage, seal_api=SimpleNamespace(enabled=False))
    results, errors, output, fields = _recognize_stages(analyzer, ctx, plan, {}, {})
    final = assemble_result(ctx, plan, results, errors, output, fields, None, None, {})

    assert final["fields"]["客户名称"] == "测试客户"
    assert final["fields"]["要求到货"] == required
    assert final["fields"]["签章要求"] == "测试收货章"
    assert final["internal_fields"]["客户订单号"] == "123"
    assert received["date"]["要求到货"] == "2025-01-04"
    assert received["seal"]["要求到货"] == required
    for stage in ("date", "seal"):
        assert received[stage]["签章要求"] == final["fields"]["签章要求"]
    assert (local, remote) == original


def test_rejected_field_warning_is_rebuilt_from_chosen_metadata(tmp_path):
    plan = config()
    plan["fields"] = ["paddle_v6", "paddle_v6_server"]
    warning = issue("low_confidence", "fields", "关键字段低置信度：客户名称")
    unrelated = issue("document_routing", "document", "页脚待核对")
    variants = [
        {"method": "paddle_v6", "result": {
            "fields": {"客户名称": "错误客户", "要求到货": "2025-01-04", "签章要求": "收货章"},
            "field_metadata": {"客户名称": {"confidence": .4, "low_confidence": True}},
            "stage_review_issues": [warning, unrelated],
            "stage_review_reasons": [warning["message"], unrelated["message"]],
        }},
        {"method": "paddle_v6_server", "result": {
            "fields": {"客户名称": "正确客户"},
            "field_metadata": {"客户名称": {"confidence": .98, "low_confidence": False}},
        }},
    ]
    before = deepcopy(variants)
    results = {stage: [] for stage in STAGES}
    results["fields"] = variants
    final = assemble_result(context(tmp_path), plan, results, [], {}, {}, None, None, {})

    assert final["fields"]["客户名称"] == "正确客户"
    assert final["field_metadata"]["客户名称"]["source"] == "paddle_v6_server"
    assert unrelated in final["review_issues"]
    assert not any(item["code"] in {"provider_conflict", "low_confidence"} for item in final["review_issues"])
    assert warning["message"] not in final["stage_review_reasons"]
    assert variants == before


def test_missing_critical_fields_still_need_review_after_selection(tmp_path):
    plan = config()
    plan["fields"] = ["paddle_v6"]
    results = {stage: [] for stage in STAGES}
    results["fields"] = [{"method": "paddle_v6", "result": {
        "fields": {"客户名称": "", "要求到货": "2025-01-04", "签章要求": "收货章"},
        "stage_review_issues": [issue("low_confidence", "fields", "关键字段低置信度：客户名称")],
    }}]
    final = assemble_result(context(tmp_path), plan, results, [], {}, {}, None, None, {})
    assert any(item["code"] == "low_confidence" and "客户名称" in item["message"]
               for item in final["review_issues"])
