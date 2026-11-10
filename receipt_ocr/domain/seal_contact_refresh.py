"""Repair saved real hotline-only mismatches without recognizing any image again."""
from copy import deepcopy

from .decision import decide_overall, has_provider_failure
from .evidence_selection import inactive
from .parsing.parsing_seals import compare_seal_text_strict, normalize_seal_text_strict
from .parsing.seal_information_fields import repair_hotline_field_match
from .review_policy import has_human_edits
from .seal_provider_selection import combine_seal_provider_checks


def _details(variant):
    return variant.get('details') or variant.get('result') or {}


def _real_trace(trace):
    return (trace.get('mode') == 'real' and trace.get('simulated') is False
            and trace.get('status') == 'completed' and not inactive(trace))


def _usable(variant, current):
    details = _details(variant)
    check = details.get('seal_check') or {}
    trace = details.get('danzhengtong') or current.get('danzhengtong') or {}
    return bool(check) and not any(inactive(value) for value in
                                  (variant, details, check, trace if variant.get('method') == 'danzhengtong' else {}))


def refresh_seal_contact_results(current):
    """Recompare only a reliable real DanZhengTong reading with a valid hotline.

    Provider text, images and traces remain intact. Persisting callers must also
    protect review history and active jobs while holding their write lock.
    """
    result = deepcopy(current)
    variants = (result.get('recognition_variants') or {}).get('seal') or []
    if (has_human_edits(current) or inactive(current)
            or any(has_human_edits(_details(variant)) for variant in variants)):
        return result
    changed = False
    for variant in variants:
        details = _details(variant)
        old = details.get('seal_check') or {}
        trace = details.get('danzhengtong') or result.get('danzhengtong') or {}
        if (variant.get('method') != 'danzhengtong' or not _usable(variant, result)
                or old.get('status') == '匹配' or old.get('reliable') is not True
                or not _real_trace(trace)):
            continue
        required = old.get('requirement') or (result.get('fields') or {}).get('签章要求') or ''
        active_required = (result.get('fields') or {}).get('签章要求')
        if active_required and normalize_seal_text_strict(active_required) != normalize_seal_text_strict(required):
            continue  # A stale requirement needs its own dependency refresh.
        recognized = old.get('recognized') or ''
        if not repair_hotline_field_match(required, recognized):
            continue
        comparison = compare_seal_text_strict(required, [recognized])
        if (comparison.get('status') != '匹配' or comparison.get('reliable') is not True
                or comparison.get('comparison_policy') != 'strict_information_fields'
                or '电话' not in (comparison.get('field_comparison') or {}).get('additional_fields', [])):
            continue
        # These are derived comparison fields. The provider's text, confidence,
        # candidates, backend and original API response are never replaced.
        updated = {**old, **{key: deepcopy(comparison[key]) for key in
                   ('status', 'message', 'score', 'requirement_coverage', 'company_score',
                    'company_conflict', 'comparison_policy', 'field_comparison')}}
        details['seal_check'] = updated
        variant['value'] = [recognized, updated['status']]
        changed = True
    if not changed:
        return result

    previous = current.get('seal_check') or {}
    issues = result.get('review_issues')
    standard_block = (previous.get('comparison_policy') == 'manual_standard_text_and_shape'
                      or (result.get('seal_standard_check') or {}).get('status') == 'unmatched'
                      or any(item.get('scope') == 'seal' and item.get('code') == 'seal_standard'
                             for item in issues or []))
    if standard_block:
        return result  # The independent reading cannot waive a standard's shape gate.
    acceptance = (result.get('recognition_config') or {}).get('acceptance') or {}
    mode = acceptance.get('seal_match_mode', 'any')
    checks = [_details(variant)['seal_check'] for variant in variants if _usable(variant, result)]
    combined = combine_seal_provider_checks(checks, acceptance.get('seal_pass_standard', 'any_exact'),
                                           match_mode=mode)
    if combined is None:
        return result
    if mode == 'none':
        combined.update(status='未核对', reliable=True, message='按设置跳过客户印章核对')
    elif mode == 'all' and len(checks) != len(variants):
        combined.update(status='需人工复核', reliable=False,
                        message='全部识别结果匹配模式下，客户印章结果未全部匹配')
    result['seal_check'] = combined
    if combined.get('status') != '匹配' or combined.get('reliable') is not True:
        return result

    removed = [item for item in issues or [] if item.get('scope') == 'seal'
               and item.get('code') in {'evidence_unreliable', 'provider_conflict'}]
    messages = {item.get('message') for item in removed}
    if isinstance(issues, list):
        result['review_issues'] = [item for item in issues if item not in removed]
    for key in ('review_reasons', 'stage_review_reasons'):
        if key in result:
            result[key] = [message for message in result[key] if message not in messages]
    overall = ('识别失败' if has_provider_failure(result) else decide_overall(
        result.get('date_check') or {}, combined, result.get('review_reasons') or [], acceptance,
        result.get('signature_check') or {}, review_issues=result.get('review_issues')))
    if overall == '通过' and (result.get('seal_standard_check') or {}).get('status') == 'missing':
        overall = '需人工复核'
    result.update(overall=overall, final_result=overall,
                  review_status='待复核' if overall == '需人工复核' else '无需复核')
    return result
