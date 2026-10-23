"""Choose the strongest independent provider seal reading and keep its evidence."""

from copy import deepcopy
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict, normalize_seal_text_strict, seal_text_comparison_rank


def _provider(check):
    if check.get('dual_check'):
        return 'qingtong'
    if check.get('recognition_mode') == 'danzhengtong' or check.get('backend') == '单证通':
        return 'danzhengtong'
    if check.get('comparison_policy') in {'strict_text', 'strict_information_fields'}:
        return 'local'
    return ''


def _independent_checks(checks):
    """Unpack local readings without counting a previous winner as QingTong."""
    sources, seen = [], set()
    for check in checks:
        remote = check.get('qingtong_channel') or check
        for candidate in (remote, check.get('local_channel')):
            if not isinstance(candidate, dict) or not _provider(candidate):
                continue
            if _provider(candidate) == 'local':
                candidate = {**candidate,
                             'source': candidate.get('source') or candidate.get('backend') or '本地印章 OCR',
                             'backend': candidate.get('backend') or candidate.get('source') or '本地印章 OCR'}
            identity = tuple(str(candidate.get(key) or '') for key in
                             ('backend', 'source', 'requirement', 'recognized', 'status'))
            if identity not in seen:
                sources.append(candidate)
                seen.add(identity)
    return sources


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
    Strict local OCR participates independently at every comparison status.
    Legacy fuzzy/geometry verdicts remain outside strict text arbitration.
    """
    providers = _independent_checks(checks)
    if not providers:
        return None
    allowed = {'any_exact', 'danzhengtong_exact', 'qingtong_exact'}
    preferred = {'danzhengtong_exact': 'danzhengtong', 'qingtong_exact': 'qingtong'}.get(policy)
    candidates = [check for check in providers if _provider(check) == preferred] if preferred else providers
    winner = max(candidates or providers, key=_rank)
    base = next((check for check in providers if check.get('dual_check')), winner)
    result = deepcopy(base)
    if base.get('dual_check'):
        result['qingtong_channel'] = deepcopy(base)
        result['qingtong_channel'].pop('qingtong_channel', None)
        result['qingtong_channel'].pop('local_channel', None)
    local = [check for check in providers if _provider(check) == 'local']
    if local:
        result['local_channel'] = deepcopy(max(local, key=_rank))
    for key in ('requirement', 'recognized', 'score', 'confidence', 'company_score',
                'company_conflict', 'status', 'comparison_policy', 'requirement_coverage', 'simulated',
                'field_comparison'):
        if key in winner:
            result[key] = deepcopy(winner[key])
        else:
            result.pop(key, None)
    result['source'] = winner.get('source') or winner.get('backend') or ''
    result['backend'] = winner.get('backend') or result['source']
    result['display_text'] = result.get('recognized', '')
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
        '部分匹配': ('全部结果匹配模式下，各来源尚未全部匹配，需人工复核'
                   if match_mode == 'all' and any(provider_matches)
                   else '没有完全匹配的结果；最匹配的印章文字仅缺少部分字，需人工复核'),
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
