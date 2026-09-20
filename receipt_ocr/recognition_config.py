"""Validated per-content recognition plans and conservative multi-provider results."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
import time

from .ocr_backends import backend_catalog, backend_route, backend_label
from .paddle_ocr import PADDLE_BACKENDS, SEAL_BACKENDS
from .recognition_scope import provider_scope
from .seal_audit_policy import seal_audit_providers_for_plan
from .document_context import DocumentContext, StageRequest
from .pipeline import attach_product_fields, complete_result
from .field_schema import PRINTED_FIELDS
from .recognition_safety import _ocr_model_config
from .execution import measure, recognition_run
from .image_processing import SealRegion, annotate_image
from .seal_provider_policy import combine_seal_provider_checks
from .seal_local_channel import local_seal_full_match, record_local_channel
from .recognition_progress import model_stage, progress_context, report_plan, report_model_progress
from .seal_orientation import (
    DEFAULT_SEAL_ORIENTATION_MODE,
    DOC_ORIENTATION_PROVIDER,
    resolve_seal_orientation_mode,
)

STAGES = ("fields", "products", "handwriting", "date", "seal")
LABELS = dict(fields="印刷字段", products="商品明细", handwriting="手写签名", date="签收日期", seal="客户印章")


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
            if method == "danzhengtong":
                continue
            if method == "qingtong":
                if stage != "seal" or not api_enabled:
                    raise ValueError("清瞳仅支持印章识别，且需要配置接口密钥")
            elif method in SEAL_BACKENDS:
                if stage != "seal" or method not in available:
                    raise ValueError(f"识别方式不可用：{method}")
            elif (
                method not in PADDLE_BACKENDS
                or method not in available
            ):
                raise ValueError(f"识别方式不可用：{method}")
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


def _value(stage, result):
    if stage == "fields":
        return {k: v for k, v in result.get("fields", {}).items() if k in PRINTED_FIELDS}
    if stage == "handwriting":
        return result.get("handwriting_fields", {})
    if stage == "products":
        return [
            row.get("values", {})
            for row in result.get("product_table", {}).get("rows", [])
        ]
    check = result.get("date_check" if stage == "date" else "seal_check", {})
    return (
        check.get("actual" if stage == "date" else "recognized", ""),
        check.get("status"),
    )


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
    # The seal stage may run a bounded second-model audit.  That audit is denied
    # before it starts unless its provider is authorised, so an explicit
    # ``SEAL_AUDIT_MODE`` widens the seal scope on purpose.  ``auto`` never
    # widens it, keeping the default behaviour unchanged.
    seal_audit_providers = seal_audit_providers_for_plan(config)
    # Route the document before any optional remote request. A failed provider
    # cannot prevent another selected provider from supplying page evidence.
    methods = list(
        dict.fromkeys(
            method
            for stage in STAGES
            for method in config[stage]
            if method not in {"qingtong", "danzhengtong"} and method not in SEAL_BACKENDS
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
                dedicated_seal = stage == "seal" and method in SEAL_BACKENDS
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
                        stage_scope |= seal_audit_providers
                        if config.get("seal_orientation", DEFAULT_SEAL_ORIENTATION_MODE) == "doc_ori":
                            stage_scope.add(DOC_ORIENTATION_PROVIDER)
                    reused = method == 'danzhengtong' and 'fixture' in danzhengtong_cache
                    with model_stage(stage, method, message='复用本张已返回结果' if reused else ''), \
                            measure(f"stage.{stage}.{method}"), provider_scope(stage_scope):
                        if method == "danzhengtong":
                            from .danzhengtong import stage_result
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
                    errors.append(f"{LABELS[stage]} / {method}：{exc}")
                    results[stage].append({"method": method, "error": str(exc)})
    return results, errors, output, previous_fields


@recognition_run
def run_configured(
    analyzer, source, preview_path, *, config, previous_fields=None, **kwargs
):
    started = time.perf_counter()
    config = validate_config(config, api_enabled=analyzer.seal_api.enabled)
    report_plan(config)
    previous_fields = deepcopy(previous_fields or {})
    context = DocumentContext(source, filename=kwargs.get("filename"))
    results, errors, output, previous_fields = _recognize_stages(
        analyzer, context, config, previous_fields, kwargs
    )
    if output is None:
        raise RuntimeError("；".join(errors))
    output = deepcopy(output)
    output.update(context.evidence())
    output["field_fallbacks"] = {}
    output["date_ocr_texts"] = []
    output["safety_policy"] = ""
    output["fields"] = previous_fields if not config["fields"] else {}
    output["field_metadata"] = {}
    output["product_table"] = {"rows": [], "status": "未执行"}
    output["date_check"] = {"status": "未执行", "actual": "", "reliable": False}
    output["seal_check"] = {"status": "未执行", "recognized": "", "reliable": False}
    output["processing_artifacts"] = {"date": [], "seals": []}
    output["recognition_variants"] = {}
    conflicts = []
    stage_reasons = []
    preview_regions = []
    preview_date_box = None
    for stage, variants in results.items():
        successful = [v for v in variants if "result" in v]
        output["recognition_variants"][stage] = [
            {
                "method": v["method"],
                "error": v.get("error"),
                "value": _value(stage, v["result"]) if "result" in v else None,
                "details": (
                    {
                        key: v["result"].get(key)
                        for key in (
                            "handwriting_fields",
                            "handwriting_metadata",
                            "fields",
                            "field_metadata",
                            "product_table",
                            "date_check",
                            "seal_check",
                            "processing_artifacts",
                            "danzhengtong",
                        )
                    }
                    if "result" in v
                    else None
                ),
            }
            for v in variants
        ]
        if not successful:
            if config[stage] and stage in ("date", "seal"):
                output[f"{stage}_check"]["status"] = "识别失败"
            if config[stage] and stage == "products":
                output["product_table"]["status"] = "识别失败"
            continue
        acceptance = config.get('acceptance') or {}
        provider_seal = (combine_seal_provider_checks(
                            [v['result'].get('seal_check', {}) for v in successful],
                            acceptance.get('seal_pass_standard', 'any_exact'),
                            match_mode=acceptance.get('seal_match_mode', 'any'))
                         if stage == 'seal' else None)
        decision_variants = ([v for v in successful if v['method'] in ({'danzhengtong'} if stage == 'date' and
                                                                         acceptance.get('date_source') == 'danzhengtong' and
                                                                         acceptance.get('date_match_mode') is None and
                                                                         any(x['method'] == 'danzhengtong' for x in successful)
                                                                         else {'qingtong', 'danzhengtong'})
                              or v['result'].get('seal_check', {}).get('dual_check')]
                             if provider_seal is not None else successful)
        if stage == 'date' and acceptance.get('date_match_mode') in {'any', 'all'} and successful:
            matching = [v for v in successful if (v['result'].get('date_check') or {}).get('status') == '匹配'
                        and (v['result'].get('date_check') or {}).get('reliable') is True]
            decision_variants = matching or successful
        if stage == 'seal' and provider_seal is None and acceptance.get('seal_match_mode') in {'any', 'all'} and successful:
            matching = [v for v in successful if (v['result'].get('seal_check') or {}).get('status') == '匹配'
                        and (v['result'].get('seal_check') or {}).get('reliable') is True]
            decision_variants = matching or successful
        primary = decision_variants[0]["result"]
        for variant in decision_variants:
            if provider_seal is None or not provider_seal.get('reliable'):
                stage_reasons.extend(variant["result"].get("stage_review_reasons", []))
        if stage == "fields":
            output["fields"] = primary.get("fields", {})
            output["field_metadata"] = primary.get("field_metadata", {})
            output["field_fallbacks"] = primary.get("field_fallbacks", {})
            requirement_artifacts = [
                artifact for variant in successful
                for artifact in variant["result"].get("processing_artifacts", {})
                .get("signature_requirement", [])
            ]
            if requirement_artifacts:
                output["processing_artifacts"]["signature_requirement"] = requirement_artifacts
        elif stage == "handwriting":
            output["fields"].update(primary.get("handwriting_fields", {}))
            output["field_metadata"].update(primary.get("handwriting_metadata", {}))
        elif stage == "products":
            output["product_table"] = primary.get("product_table", {})
        else:
            output[f"{stage}_check"] = deepcopy(primary.get(f"{stage}_check", {}))
            artifact_key = "date" if stage == "date" else "seals"
            output["processing_artifacts"][artifact_key] = [
                a
                for v in successful
                for a in v["result"]
                .get("processing_artifacts", {})
                .get(artifact_key, [])
            ]
            if stage == "date":
                preview_date_box = primary.get("preview_date_box")
                output["date_ocr_texts"] = primary.get("date_ocr_texts", [])
            else:
                preview_regions = primary.get("seal_regions", [])
                if provider_seal is not None:
                    output['seal_check'] = provider_seal
                if stage == 'seal':
                    # A local reading taken inside QingTong's own stamp box is
                    # independent evidence for that same stamp, so an exact
                    # match there decides the verdict under ``any``.
                    local_winner = next(
                        (
                            v["result"].get("seal_check")
                            for v in successful
                            if local_seal_full_match(v["result"].get("seal_check"))
                        ),
                        None,
                    )
                    if local_winner is not None:
                        output['seal_check'] = record_local_channel(
                            output['seal_check'], local_winner,
                            acceptance.get('seal_match_mode', 'any'),
                        )
            if stage == 'date' and acceptance.get('date_match_mode') == 'none':
                output['date_check'].update(status='未核对', reliable=True, message='按设置跳过签收日期核对')
            if stage == 'seal' and acceptance.get('seal_match_mode') == 'none':
                output['seal_check'].update(status='未核对', reliable=True, message='按设置跳过客户印章核对')
            if stage == 'seal' and provider_seal is None and acceptance.get('seal_match_mode') == 'all':
                all_checks = [variant.get('result', {}).get('seal_check') for variant in variants]
                all_match = bool(all_checks) and len(all_checks) == len(successful) == len(variants) and all(
                    check.get('status') == '匹配' and check.get('reliable') is True
                    for check in all_checks if isinstance(check, dict)
                )
                if not all_match:
                    output['seal_check'].update(
                        status='需人工复核', reliable=False,
                        message='全部识别结果匹配模式下，客户印章结果未全部匹配',
                    )
            if stage == 'date' and acceptance.get('date_match_mode') == 'all':
                all_checks = [variant.get('result', {}).get('date_check') for variant in variants]
                all_match = bool(all_checks) and len(all_checks) == len(successful) == len(variants) and all(
                    check.get('status') == '匹配' and check.get('reliable') is True
                    for check in all_checks if isinstance(check, dict)
                )
                if not all_match:
                    output['date_check'].update(
                        status='需人工复核', reliable=False,
                        message='全部识别结果匹配模式下，签收日期结果未全部匹配',
                    )
        output["safety_policy"] = (
            primary.get("safety_policy") or output["safety_policy"]
        )
        stage_match_mode = acceptance.get(f'{stage}_match_mode')
        if provider_seal is None and stage_match_mode != 'any' and any(
            _value(stage, v["result"]) != _value(stage, primary) for v in decision_variants[1:]
        ):
            conflicts.append(f"{LABELS[stage]}：多种识别方式结果不一致")
            if stage in ("date", "seal"):
                output[f"{stage}_check"].update(status="需人工复核", reliable=False)
        if provider_seal is None and stage in ("date", "seal") and stage_match_mode != 'any' and any(
            not v["result"].get(f"{stage}_check", {}).get("reliable")
            for v in decision_variants
        ):
            output[f"{stage}_check"]["reliable"] = False
    attach_product_fields(output)
    if output.get('seal_check', {}).get('provider_comparison'):
        # One complete channel suffices; other channel failures stay visible
        # in evidence but cannot veto that match. Local OCR is supplementary.
        matched = output['seal_check'].get('status') == '匹配' and output['seal_check'].get('reliable')
        ignored = {f"{LABELS['seal']} / {v['method']}：{v['error']}" for v in results['seal']
                   if 'error' in v and (matched or v['method'] not in {'qingtong', 'danzhengtong'})}
        errors = [error for error in errors if error not in ignored]
    reasons = context.routing_reasons + errors + conflicts + stage_reasons
    for stage, field in [("date", "要求到货"), ("seal", "签章要求")]:
        if config[stage] and output[f"{stage}_check"].get('status') == '识别失败':
            output[f"{stage}_check"]['message'] = '；'.join(v['error'] for v in results[stage] if v.get('error'))
        elif config[stage] and not output["fields"].get(field):
            reasons.append(f"{LABELS[stage]}缺少比对依据：{field}")
            output[f"{stage}_check"].update(status="缺少比对依据", reliable=False)
        elif config[stage] and not output[f"{stage}_check"].get("reliable"):
            reasons.append(f"{LABELS[stage]}尚未可靠识别")
    for variants in results.values():
        for variant in variants:
            trace = variant.get("result", {}).get("danzhengtong")
            if trace:
                output["danzhengtong"] = deepcopy(trace)
    output["recognition_config"] = config
    output["recognition_status"] = {
        stage: (
            "未执行"
            if not config[stage]
            else (
                "识别失败"
                if not any("result" in v for v in results[stage])
                else "已执行"
            )
        )
        for stage in STAGES
    }
    reasons = list(dict.fromkeys(reasons))
    output["review_reasons"] = reasons
    output["ocr_backend"] = kwargs.get("ocr_backend") or "custom"
    primary_backends = {"page": context.primary_backend or "skipped"}
    for stage in ("date", "seal"):
        primary_backends[stage] = next(
            (variant["method"] for variant in results[stage] if "result" in variant),
            "skipped",
        )
    if (
        context.document_type["type"] not in {"receipt", "unclassified"}
        and not context.has_footer
    ):
        primary_backends.update(date="skipped", seal="skipped")
    output["ocr_model_config"] = _ocr_model_config(primary_backends)
    output["ocr_stage_backends"] = {
        stage: {
            "id": method,
            "label": (
                "未执行"
                if method == "skipped"
                else "清瞳印章 API" if method == "qingtong" else backend_label(method)
            ),
        }
        for stage, method in primary_backends.items()
    }

    output["ocr_backend_label"] = "自定义识别方案"
    output["seal_recognition_mode"] = (
        "qingtong_only"
        if config["seal"] == ["qingtong"]
        else "qingtong" if "qingtong" in config["seal"] else "local"
    )
    output["seal_orientation_mode"] = config.get(
        "seal_orientation", DEFAULT_SEAL_ORIENTATION_MODE
    )
    output["seal_regions"] = preview_regions
    output["preview_date_box"] = preview_date_box
    output["stage_review_reasons"] = list(dict.fromkeys(stage_reasons))
    complete_result(output, reference_matcher=kwargs.get("reference_matcher"))
    if preview_path:
        annotate_image(
            source,
            preview_path,
            [SealRegion(**region) for region in preview_regions],
            preview_date_box,
        )
    output["processing_seconds"] = round(time.perf_counter() - started, 2)
    return output
