"""Stage dispatch and shared output assembly for both recognition entry points."""

from copy import deepcopy
from time import perf_counter

from ..domain.decision import finalize_result
from ..domain.fields.schema import project_fields
from ..application.context import DocumentContext
from ..application.requests import StageRequest
from ..imaging.contracts import SealRegion

from ..imaging.page import annotate_image
from ..providers.catalog import backend_label, backend_route, resolve_backend
from ..domain.parsing import product_table_text, LOW_CONFIDENCE_THRESHOLD
from ..runtime.safety import _ocr_model_config
from receipt_ocr.recognition.seal.api import resolve_seal_recognition_mode
from receipt_ocr.recognition.seal.orientation import DEFAULT_SEAL_ORIENTATION_MODE

from ..runtime.progress import model_stage, report_plan

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
    """Fill the stable result envelope shared by both recognition entry points.

    Stage implementations own the values they recognize. This helper only
    supplies absent containers and status slots so callers can consume legacy
    and configured results through the same shape.
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


def merge_stage(output, result):
    for key, value in result.items():
        if key == "handwriting_fields":
            output["fields"].update(deepcopy(value))
        elif key == "handwriting_metadata":
            output["field_metadata"].update(deepcopy(value))
        elif key == "processing_artifacts":
            output[key].update(deepcopy(value))
        elif key == "stage_review_reasons":
            output[key].extend(value)
        elif key == "safety_policy":
            output[key] = value or output.get(key, "")
        else:
            output[key] = deepcopy(value)


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


def render_preview(source, preview_path, output):
    if preview_path:
        annotate_image(
            source,
            preview_path,
            [SealRegion(**r) for r in output["seal_regions"]],
            output.get("preview_date_box"),
        )


def run_legacy(
    analyzer,
    source,
    preview_path=None,
    *,
    artifact_dir=None,
    artifact_url_prefix="",
    ocr_backend=None,
    seal_recognition_mode=None,
    _targets=None,
    _route=None,
    _previous_fields=None,
    _seal_api_future=None,
    filename=None,
    reference_matcher=None,
):
    started = perf_counter()
    targets = set(STAGES) if _targets is None else set(_targets)
    if targets - set(STAGES):
        raise ValueError("未知识别阶段")
    backend = resolve_backend(ocr_backend)
    route = _route or backend_route(backend)
    mode = resolve_seal_recognition_mode(seal_recognition_mode)
    plan = {stage: [route['page' if stage in {'fields', 'handwriting', 'products'} else stage]] for stage in STAGES if stage in targets}
    report_plan(plan)
    context = DocumentContext(source, filename=filename)
    output = empty_result(context, _previous_fields)
    with context.image_scope():
        for stage in STAGES:
            if stage not in targets:
                continue
            request = StageRequest(
                route,
                deepcopy(output["fields"]),
                artifact_dir,
                artifact_url_prefix,
                mode,
                _seal_api_future,
            )
            with model_stage(stage, plan[stage][0]):
                merge_stage(output, analyzer.run_stage(context, stage, request))
        attach_product_fields(output)
    output.update(context.evidence())
    output.update(
        ocr_backend=backend,
        ocr_backend_label=backend_label(backend),
        seal_recognition_mode=mode,
        ocr_model_config=_ocr_model_config(route),
        ocr_stage_backends={
            stage: {"id": value, "label": backend_label(value)}
            for stage, value in route.items()
        },
    )
    normalize_result_contract(output, executed_stages=targets)
    if (
        context.document_type["type"] not in {"receipt", "unclassified"}
        and not context.has_footer
    ):
        for stage in ("date", "seal"):
            output["ocr_stage_backends"][stage] = {
                "id": "skipped",
                "label": "未执行（文档类型分流）",
            }
    output["review_reasons"] = context.routing_reasons + output["stage_review_reasons"]
    complete_result(output, reference_matcher=reference_matcher)
    with context.image_scope():
        render_preview(context.source, preview_path, output)
    output["processing_seconds"] = round(perf_counter() - started, 2)
    return output


def complete_result(result: dict, *, reference_matcher=None) -> dict:
    from ..stages import seal as stage_seal

    stage_seal.complete_evidence(result, reference_matcher)
    project_fields(result)
    return finalize_result(result)
