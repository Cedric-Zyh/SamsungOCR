"""Combine stage observations and acceptance decisions into one receipt result."""
from copy import deepcopy
from ..domain.stages import STAGES, STAGE_LABELS as LABELS
from ..domain.issues import issue
from ..domain.fields.schema import PRINTED_FIELDS
from ..providers.catalog import backend_label
from ..runtime.safety import _ocr_model_config
from ..imaging.contracts import SealRegion
from ..imaging.page import annotate_image
from ..recognition.seal.orientation import DEFAULT_SEAL_ORIENTATION_MODE
from ..recognition.seal.postprocess.providers import combine_seal_provider_checks
from ..recognition.seal.postprocess.channels import local_seal_full_match, record_local_channel
from .pipeline import attach_product_fields, complete_result, normalize_result_contract

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


def select_variants(stage, successful, acceptance):
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
    return provider_seal, decision_variants, primary


def apply_match_mode(output, stage, variants, successful, provider_seal, acceptance):
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


def merge_primary(output, stage, primary, successful, variants, provider_seal, acceptance):
    preview_regions, preview_date_box = [], None
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
        apply_match_mode(output, stage, variants, successful, provider_seal, acceptance)
    return preview_regions, preview_date_box


def finish_result(output, context, config, results, reasons, issues, stage_reasons, preview_regions, preview_date_box, source, preview_path, kwargs):
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
    output["review_issues"] = issues
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
    normalize_result_contract(
        output,
        executed_stages={stage for stage in STAGES if config[stage]},
        recognition_config=config,
    )
    complete_result(output, reference_matcher=kwargs.get("reference_matcher"))
    if preview_path:
        annotate_image(
            source,
            preview_path,
            [SealRegion(**region) for region in preview_regions],
            preview_date_box,
        )
    return output


def assemble_result(context, config, results, errors, output, previous_fields, source, preview_path, kwargs):
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
    stage_issues = []
    conflicts_issues = []
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
        provider_seal, decision_variants, primary = select_variants(stage, successful, acceptance)
        for variant in decision_variants:
            if provider_seal is None or not provider_seal.get('reliable'):
                stage_reasons.extend(variant["result"].get("stage_review_reasons", []))
                stage_issues.extend(variant["result"].get("stage_review_issues", []))
        regions, date_box = merge_primary(output, stage, primary, successful, variants, provider_seal, acceptance)
        if stage == "date":
            preview_date_box = date_box
        elif stage == "seal":
            preview_regions = regions
        output["safety_policy"] = (
            primary.get("safety_policy") or output["safety_policy"]
        )
        stage_match_mode = acceptance.get(f'{stage}_match_mode')
        if provider_seal is None and stage_match_mode != 'any' and any(
            _value(stage, v["result"]) != _value(stage, primary) for v in decision_variants[1:]
        ):
            conflicts.append(f"{LABELS[stage]}：多种识别方式结果不一致")
            conflicts_issues.append(issue("provider_conflict", stage, conflicts[-1]))
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
        ignored = {v['method'] for v in results['seal']
                   if 'error' in v and (matched or v['method'] not in {'qingtong', 'danzhengtong'})}
        errors = [error for error in errors if not (error["scope"] == "seal" and error["provider"] in ignored)]
    issues = context.routing_issues
    issues.extend(errors + conflicts_issues + stage_issues)
    reasons = context.routing_reasons + [error["message"] for error in errors] + conflicts + stage_reasons
    for stage, field in [("date", "要求到货"), ("seal", "签章要求")]:
        if config[stage] and output[f"{stage}_check"].get('status') == '识别失败':
            output[f"{stage}_check"]['message'] = '；'.join(v['error'] for v in results[stage] if v.get('error'))
        elif config[stage] and not output["fields"].get(field):
            reasons.append(f"{LABELS[stage]}缺少比对依据：{field}")
            issues.append(issue("missing_requirement", stage, reasons[-1]))
            output[f"{stage}_check"].update(status="缺少比对依据", reliable=False)
        elif config[stage] and not output[f"{stage}_check"].get("reliable"):
            reasons.append(f"{LABELS[stage]}尚未可靠识别")
            issues.append(issue("evidence_unreliable", stage, reasons[-1]))
    for variants in results.values():
        for variant in variants:
            trace = variant.get("result", {}).get("danzhengtong")
            if trace:
                output["danzhengtong"] = deepcopy(trace)
    return finish_result(output, context, config, results, reasons, issues, stage_reasons,
                         preview_regions, preview_date_box, source, preview_path, kwargs)
