"""Validated per-content recognition plans and conservative multi-provider results."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
import time

from ..providers.catalog import backend_catalog, backend_route, backend_label
from ..providers.registry import PROVIDERS
from ..providers.selection import validate_stage_provider
from ..providers.text import text_recognizer_scope
from ..domain.stages import STAGES, STAGE_LABELS as LABELS
from ..domain.issues import issue

SEAL_PROVIDERS = frozenset(item.id for item in PROVIDERS.of_kind("local_seal"))
from ..runtime.scope import provider_scope
from receipt_ocr.recognition.seal.reading_policy import secondary_read_providers_for_plan
from ..application.context import DocumentContext
from ..application.requests import StageRequest
from ..runtime.execution import measure, recognition_run

from ..runtime.progress import model_stage, progress_context, report_plan, report_model_progress
from receipt_ocr.recognition.seal.orientation import (
    DEFAULT_SEAL_ORIENTATION_MODE,
    DOC_ORIENTATION_PROVIDER,
    resolve_seal_orientation_mode,
)



def default_plan(backend=None, seal_mode=None):
    from ..recognition.seal.api import resolve_seal_recognition_mode

    route = backend_route(backend)
    mode = resolve_seal_recognition_mode(seal_mode)
    plan = {stage: [route['page' if stage in {'fields', 'handwriting', 'products'} else stage]]
            for stage in STAGES}
    if mode == "qingtong_only":
        plan["seal"] = ["qingtong"]
    elif mode == "qingtong":
        plan["seal"] = ["qingtong", route["seal"]]
    return plan


def validate_config(config, *, api_enabled=True):
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) - set(STAGES) - {'acceptance', 'seal_orientation'}:
        raise ValueError("识别配置格式不正确")
    available = {row["id"] for row in backend_catalog() if row["available"]}
    cleaned = {}
    for stage in STAGES:
        methods = config.get(stage, [])
        if not isinstance(methods, list) or any(
            not isinstance(x, str) for x in methods
        ):
            raise ValueError("识别方式必须为列表")
        methods = list(dict.fromkeys(methods))
        for method in methods:
            validate_stage_provider(
                stage, method, available=available, api_enabled=api_enabled,
            )
        cleaned[stage] = methods
    if "seal_orientation" in config:
        cleaned["seal_orientation"] = resolve_seal_orientation_mode(
            config.get("seal_orientation")
        )
    has_acceptance = 'acceptance' in config
    acceptance = config.get('acceptance') or {}
    if not isinstance(acceptance, dict):
        raise ValueError("默认通过标准格式不正确")
    seal_standard = acceptance.get('seal_pass_standard', 'any_exact')
    if seal_standard not in {'any_exact', 'danzhengtong_exact', 'qingtong_exact'}:
        raise ValueError("印章默认通过标准不正确")
    if has_acceptance:
        def mode(name, default='any'):
            return acceptance.get(name) if acceptance.get(name) in {'any', 'all', 'none'} else default
        cleaned['acceptance'] = {
            'seal_match_mode': mode('seal_match_mode'),
            'date_match_mode': mode('date_match_mode'),
            'signature_match_mode': mode('signature_match_mode', 'none'),
            'reject_mode': acceptance.get('reject_mode') if acceptance.get('reject_mode') in {'any_mismatch', 'all_mismatch', 'none'} else 'none',
            'seal_pass_standard': seal_standard,
            'date_source': 'danzhengtong',
            # ``check`` preserves the historical blocking behavior. ``ignore``
            # keeps low-confidence evidence visible but does not block pass.
            'low_confidence_mode': acceptance.get('low_confidence_mode')
            if acceptance.get('low_confidence_mode') in {'check', 'ignore'} else 'check',
        }
    if not any(cleaned.get(stage) for stage in STAGES):
        raise ValueError("请至少选择一项识别内容及识别方式")
    return cleaned


@contextmanager
def _overlap_seal_request(analyzer, source, config):
    """Upload once while local stages run; comparison still waits for fields."""
    recognize = getattr(analyzer.seal_api, "recognize", None)
    has_local_work = any(
        method != "qingtong" for stage in STAGES for method in config.get(stage, [])
    )
    if (
        "qingtong" not in config["seal"]
        or not has_local_work
        or not callable(recognize)
    ):
        yield None
        return

    def request_seal():
        with progress_context(method='qingtong', method_label='清瞳', target='客户印章',
                              target_id='seal', channel='parallel'):
            report_model_progress('seal', 'started', message='并行上传与印章识别')
            started = time.perf_counter()
            try:
                with provider_scope({"qingtong"}):
                    result = recognize(source)
            except Exception:
                report_model_progress('seal', 'failed',
                                      elapsed_seconds=round(time.perf_counter() - started, 2))
                raise
            report_model_progress('seal', 'completed',
                                  elapsed_seconds=round(time.perf_counter() - started, 2))
            return result

    # Only network I/O runs here. Paddle stays on its existing serialized path.
    # Joining the worker also prevents a completed/failed run leaking work into
    # the next receipt. The API client's existing timeout still applies.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt-seal") as pool:
        yield pool.submit(copy_context().run, request_seal)


def _recognize_stages(analyzer, context, config, previous_fields, kwargs):
    base_backend = kwargs.get("ocr_backend")
    results = {stage: [] for stage in STAGES}
    errors = []
    output = None
    danzhengtong_cache = {}
    # An optional additional OCR read is authorized here.  The seal stage
    # itself remains provider-neutral and never knows how the reader runs.
    seal_secondary_readers = secondary_read_providers_for_plan(config)
    # Route the document before any optional remote request. A failed provider
    # cannot prevent another selected provider from supplying page evidence.
    methods = list(
        dict.fromkeys(
            method
            for stage in STAGES
            for method in config[stage]
            if method not in {"qingtong", "danzhengtong"} and method not in SEAL_PROVIDERS
        )
    )
    for method in methods:
        try:
            with model_stage('routing', method), provider_scope({method}):
                context.page(method)
            if context.document_type["reliable"]:
                break
        except Exception:
            continue  # The selected stage records its own failure below.
    remote_config = config if context.allows_remote_seal else {**config, "seal": []}
    with _overlap_seal_request(analyzer, context.source, remote_config) as seal_future:
        for stage in STAGES:
            for method in config[stage]:
                dedicated_seal = stage == "seal" and method in SEAL_PROVIDERS
                backend = (
                    method
                    if method != "qingtong"
                    else backend_route(base_backend)["page"]
                )
                route = (
                    dict(
                        page=context.primary_backend or backend_route(base_backend)["page"],
                        date=context.primary_backend or backend_route(base_backend)["date"],
                        seal=method,
                    )
                    if dedicated_seal
                    else dict(page=backend, date=backend, seal=backend)
                )
                directory = kwargs.get("artifact_dir")
                prefix = kwargs.get("artifact_url_prefix", "")
                if directory:
                    directory = Path(directory) / f"{stage}-{method}"
                    prefix = prefix.rstrip("/") + f"/{stage}-{method}"
                request = StageRequest(
                    route,
                    deepcopy(previous_fields),
                    directory,
                    prefix,
                    "qingtong_only" if method == "qingtong" else "local",
                    # A local seal run reuses the request QingTong already made
                    # so it can read inside the API's own stamp box.
                    seal_future
                    if method == "qingtong" or (stage == "seal" and method != "danzhengtong")
                    else None,
                    config.get("acceptance") or {},
                    seal_orientation_mode=config.get(
                        "seal_orientation", DEFAULT_SEAL_ORIENTATION_MODE
                    ),
                )
                try:
                    stage_scope = {method}
                    if dedicated_seal:
                        stage_scope.add(route["page"])
                    if stage == "seal":
                        stage_scope |= seal_secondary_readers
                        # Only the doc_ori mode calls the four-way document
                        # classifier.  ``combined`` picks its coarse quarter-turn
                        # from the stamp-type row itself, so it must not claim a
                        # provider it never uses.
                        if config.get("seal_orientation", DEFAULT_SEAL_ORIENTATION_MODE) == "doc_ori":
                            stage_scope.add(DOC_ORIENTATION_PROVIDER)
                    reused = method == 'danzhengtong' and 'fixture' in danzhengtong_cache
                    with model_stage(stage, method, message='复用本张已返回结果' if reused else ''), \
                            measure(f"stage.{stage}.{method}"), provider_scope(stage_scope):
                        if method == "danzhengtong":
                            from ..danzhengtong import stage_result
                            result = stage_result(context, stage, danzhengtong_cache, previous_fields)
                        else:
                            result = analyzer.run_stage(context, stage, request)
                    results[stage].append({"method": method, "result": result})
                    if output is None:
                        output = deepcopy(result)
                    if (
                        stage == "fields"
                        and sum("result" in v for v in results[stage]) == 1
                    ):
                        previous_fields = deepcopy(result.get("fields", {}))
                except Exception as exc:
                    errors.append(issue("provider_failure", stage, f"{LABELS[stage]} / {method}：{exc}", provider=method))
                    results[stage].append({"method": method, "error": str(exc)})
    return results, errors, output, previous_fields


@recognition_run
def run_configured(analyzer, source, preview_path=None, *, config=None, previous_fields=None, **kwargs):
    from .assembly import assemble_result

    started = time.perf_counter()
    config = validate_config(
        config if config is not None else default_plan(
            kwargs.get("ocr_backend"), kwargs.get("seal_recognition_mode")
        ), api_enabled=analyzer.seal_api.enabled,
    )
    report_plan(config)
    previous_fields = deepcopy(previous_fields or {})
    context = DocumentContext(source, filename=kwargs.get("filename"),
                              text_recognizer=getattr(analyzer, "text_recognizer", None))
    with context.image_scope(), text_recognizer_scope(context.text_recognizer):
        results, errors, output, previous_fields = _recognize_stages(
            analyzer, context, config, previous_fields, kwargs
        )
        if output is None:
            raise RuntimeError("；".join(error["message"] for error in errors))
        output = assemble_result(context, config, results, errors, output, previous_fields,
                                 source, preview_path, kwargs)
    output["processing_seconds"] = round(time.perf_counter() - started, 2)
    return output
