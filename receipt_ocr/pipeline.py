"""Stage dispatch and shared output assembly for both recognition entry points."""

from copy import deepcopy
from time import perf_counter

from . import stage_fields, stage_products, stage_date, stage_seal, stage_handwriting
from .decision import finalize_result
from .field_schema import project_fields
from .document_context import DocumentContext, StageRequest
from .image_processing import SealRegion, annotate_image
from .ocr_backends import backend_label, backend_route, resolve_backend
from .parser import product_table_text, LOW_CONFIDENCE_THRESHOLD
from .recognition_safety import _ocr_model_config
from .seal_api import resolve_seal_recognition_mode

STAGES = ("fields", "products", "handwriting", "date", "seal")


def execute_stage(analyzer, context, stage, request):
    if stage == "fields":
        result = stage_fields.execute(context, request)
    elif stage == "products":
        result = stage_products.execute(context, request)
    elif stage == "handwriting":
        result = stage_handwriting.execute(context, request)
    elif stage == "date":
        result = stage_date.execute(context, request, analyzer._recognize_receipt_date)
    elif stage == "seal":
        result = stage_seal.execute(
            context, request, analyzer._recognize_local_seals, analyzer.seal_api
        )
    else:
        raise ValueError(f"未知识别阶段：{stage}")
    return {**context.evidence(), **result}


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
    context = DocumentContext(source, filename=filename)
    output = empty_result(context, _previous_fields)
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
    render_preview(context.source, preview_path, output)
    output["processing_seconds"] = round(perf_counter() - started, 2)
    return output


def complete_result(result: dict, *, reference_matcher=None) -> dict:
    stage_seal.complete_evidence(result, reference_matcher)
    project_fields(result)
    return finalize_result(result)
