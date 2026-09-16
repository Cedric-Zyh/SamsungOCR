"""Choose the strongest independent provider seal reading and keep its evidence."""

from copy import deepcopy
from .parsing_seals import compare_seal_text_strict, normalize_seal_text_strict, seal_text_comparison_rank


def _provider(check):
    if check.get('dual_check'):
        return 'qingtong'
    if check.get('recognition_mode') == 'danzhengtong' or check.get('backend') == '单证通':
        return 'danzhengtong'
    return ''


def _rank(check):
    actual = normalize_seal_text_strict(check.get('recognized') or '')
    comparison = check if check.get('requirement_coverage') is not None else {
        **compare_seal_text_strict(check.get('requirement') or '', [check.get('recognized') or '']),
        'status': check.get('status'),
    }
    status = check.get('status')
    # Simulated or failed readings remain evidence, never the winning verdict.
    usable = bool(actual and not check.get('simulated') and status in {'匹配', '部分匹配', '不匹配'})
    rank, coverage, similarity = seal_text_comparison_rank(comparison)
    return rank if usable else 0, coverage, similarity


def combine_seal_provider_checks(checks, policy='any_exact', match_mode='any'):
    """Any exact real channel wins; otherwise display the best partial/mismatch.

    QingTong already selects its best template/OCR channel. Its full detected
    stamp evidence stays attached even when DanZhengTong supplies the winner.
    Local fuzzy/geometry results do not add a fourth vote to this policy.
    """
    providers = [check for check in checks if _provider(check)]
    if not providers:
        return None
    allowed = {'any_exact', 'danzhengtong_exact', 'qingtong_exact'}
    preferred = {'danzhengtong_exact': 'danzhengtong', 'qingtong_exact': 'qingtong'}.get(policy)
    candidates = [check for check in providers if _provider(check) == preferred] if preferred else providers
    winner = max(candidates or providers, key=_rank)
    base = next((check for check in providers if check.get('dual_check')), winner)
    result = deepcopy(base)
    for key in ('requirement', 'recognized', 'score', 'confidence', 'company_score',
                'company_conflict', 'status', 'comparison_policy', 'requirement_coverage', 'simulated'):
        if key in winner:
            result[key] = deepcopy(winner[key])
        else:
            result.pop(key, None)
    result['source'] = winner.get('source') or winner.get('backend') or ''
    result['backend'] = winner.get('backend') or result['source']
    status = winner.get('status')
    match_mode = 'all' if match_mode == 'all' else 'any'
    provider_matches = [check.get('status') == '匹配' and check.get('reliable') is True
                        and not check.get('simulated') for check in providers]
    if match_mode == 'all' and providers and not all(provider_matches):
        if any(provider_matches):
            status = '部分匹配'
            result['status'] = status
        result['reliable'] = False
    else:
        result['reliable'] = (
            winner.get('reliable') is True and not winner.get('simulated') if status == '匹配'
            else status == '不匹配' and all(check.get('reliable') is True and not check.get('simulated') for check in providers)
        )
    result['all_recognized'] = list(dict.fromkeys(
        text for check in providers for text in [*(check.get('all_recognized') or []), check.get('recognized', '')] if text
    ))
    result['message'] = {
        '匹配': f"{result['source']}的印章文字完全符合签章要求，印章匹配",
        '部分匹配': '没有完全匹配的结果；最匹配的印章文字仅缺少部分字，需人工复核',
        '不匹配': '各来源均未完全匹配签章要求，展示其中最匹配的印章文字',
    }.get(status, winner.get('message', '未识别到可比对的印章文字'))
    result['provider_comparison'] = {
        'policy': policy if policy in allowed else 'any_exact',
        'match_mode': match_mode,
        'selected_source': result['source'],
        'sources': [{key: deepcopy(check.get(key)) for key in
                     ('source', 'backend', 'recognized', 'status', 'reliable', 'score', 'requirement_coverage', 'simulated')}
                    for check in providers],
    }
    return result
