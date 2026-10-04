"""Stage dispatch and output contract for the unified recognition pipeline."""

from copy import deepcopy

from ..domain.decision import finalize_result
from ..domain.fields.schema import project_fields

from ..domain.parsing import product_table_text, LOW_CONFIDENCE_THRESHOLD
from receipt_ocr.recognition.seal.orientation import DEFAULT_SEAL_ORIENTATION_MODE


from ..domain.stages import STAGES
from ..api.serializers import stage_result_payload
from .stage_runner import recognize_stage


def execute_stage(analyzer, context, stage, request):
    result = recognize_stage(analyzer, context, stage, request)
    return {**context.evidence(), **stage_result_payload(result)}


def empty_result(context, fields=None):
    return {
        **context.evidence(),
        "fields": deepcopy(fields or {}),
        "field_metadata": {},
        "field_fallbacks": {},
        "product_table": {"rows": [], "status": "未执行"},
        "date_check": {
            "status": "未执行",
            "actual": "",
            "reliable": False,
            "confidence": 0,
        },
        "seal_check": {
            "status": "未执行",
            "recognized": "",
            "reliable": False,
            "score": 0,
        },
        "processing_artifacts": {"date": [], "seals": []},
        "date_ocr_texts": [],
        "seal_regions": [],
        "preview_date_box": None,
        "stage_review_reasons": [],
        "safety_policy": "",
    }


def normalize_result_contract(
    result: dict,
    *,
    executed_stages=None,
    recognition_config=None,
) -> dict:
    """Fill the stable result envelope shared by all selected stages.

    Stage implementations own the values they recognize. This helper only
    supplies absent containers and status slots for partially selected plans.
    """
    executed = set(executed_stages or ())
    result.setdefault("fields", {})
    result.setdefault("field_metadata", {})
    result.setdefault("field_fallbacks", {})
    result.setdefault("product_table", {"rows": [], "status": "未执行"})
    result.setdefault(
        "date_check",
        {"status": "未执行", "actual": "", "reliable": False, "confidence": 0},
    )
    result.setdefault(
        "seal_check",
        {"status": "未执行", "recognized": "", "reliable": False, "score": 0},
    )
    result.setdefault("processing_artifacts", {"date": [], "seals": []})
    result.setdefault("date_ocr_texts", [])
    result.setdefault("seal_regions", [])
    result.setdefault("preview_date_box", None)
    result.setdefault("stage_review_reasons", [])
    result.setdefault("review_reasons", [])
    result.setdefault("safety_policy", "")
    result.setdefault("recognition_variants", {})
    result.setdefault("recognition_config", deepcopy(recognition_config))
    result.setdefault(
        "recognition_status",
        {stage: ("已执行" if stage in executed else "未执行") for stage in STAGES},
    )
    result.setdefault("seal_orientation_mode", DEFAULT_SEAL_ORIENTATION_MODE)
    return result




def attach_product_fields(output):
    table = output.get("product_table", {})
    if not table.get("rows"):
        return
    text = product_table_text(table)
    output["fields"]["商品明细原文"] = text
    if output.get("field_metadata"):
        confidence = table.get("confidence", 0)
        output["field_metadata"]["商品明细原文"] = {
            "original": text,
            "value": text,
            "confidence": confidence,
            "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
            "source": "商品表格按列识别",
        }




def complete_result(result: dict, *, reference_matcher=None) -> dict:
    from ..stages import seal as stage_seal

    stage_seal.complete_evidence(result, reference_matcher)
    project_fields(result)
    return finalize_result(result)
