"""Recombine saved provider evidence without running OCR or remote requests."""
from copy import deepcopy

from .assembly import assemble_result
from .pipeline import empty_result
from .snapshots import DocumentSnapshot
from .document_evidence import selected_document_fields
from ..domain.results import StageOutcome
from ..domain.stages import STAGES
from ..domain.decision_provenance import record_evaluation, PROVENANCE_KEY
from ..domain.evidence_selection import inactive
from ..domain.saved_evidence import completed_real_trace


def empty_snapshot(config, previous_fields=None):
    return DocumentSnapshot.from_json({
        'config': config, 'results': {}, 'errors': [],
        'previous_fields': previous_fields or {}, 'context': {}, 'evidence': {},
    }).to_json()


def mark_danzhengtong_pending(result, config):
    """Retain the evaluated regular result while the selected remote lane waits."""
    from ..domain.decision import apply_pending_verdict
    output = deepcopy(result)
    output['recognition_config'] = deepcopy(config)
    for stage in STAGES:
        if 'danzhengtong' not in config[stage]:
            continue
        output.setdefault('recognition_status', {})[stage] = '等待单证通结果'
        variants = output.setdefault('recognition_variants', {}).setdefault(stage, [])
        variants[:] = [variant for variant in variants if variant.get('method') != 'danzhengtong']
        variants.append({'method': 'danzhengtong', 'pending': True,
                         'value': None, 'details': None, 'error': None})
    return apply_pending_verdict(output)


def combine_saved(source, filename, snapshot, *, remote=None, remote_seal=None, pending=False, preserve_provider_checks=False):
    from ..danzhengtong import normalize_result, stage_result

    saved = DocumentSnapshot.from_json(snapshot)
    context = saved.restore_context(source, filename)
    config, results = saved.config, saved.results
    fields = selected_document_fields(results['fields'], saved.previous_fields)
    failures = list(saved.errors)

    def failed_stage(stage, message):
        from ..domain.issues import issue
        results[stage].append(StageOutcome.failed(stage, 'danzhengtong', message))
        failures.append(issue('provider_failure', stage, f'单证通 / {stage}：{message}', provider='danzhengtong'))

    remote_failed = bool(remote and remote.get('status') in {'failed', 'unknown'})
    if remote_failed:
        for stage in STAGES:
            if ('danzhengtong' in config[stage] and stage != 'products'
                    and not (stage == 'seal' and remote_seal is not None)):
                failed_stage(stage, remote.get('error') or '单证通整页识别失败')
    if remote and not remote_failed:
        cache = {'fixture': normalize_result(remote['result']['commitResult']), 'trace': remote}
        if 'danzhengtong' in config['fields']:
            results['fields'].append(StageOutcome.completed('fields', 'danzhengtong', stage_result(context, 'fields', cache, fields)))
        fields = selected_document_fields(results['fields'], fields)
        for stage in STAGES:
            if stage != 'fields' and not (stage == 'seal' and remote_seal is not None) and 'danzhengtong' in config[stage]:
                results[stage].append(StageOutcome.completed(stage, 'danzhengtong', stage_result(context, stage, cache, fields)))
    if remote_seal is not None and 'danzhengtong' in config['seal']:
        from ..danzhengtong_seal import build_stage_result
        if remote_seal.get('preparation_error'):
            failed_stage('seal', '印章准备失败：' + remote_seal['preparation_error'])
        else:
            results['seal'].append(StageOutcome.completed('seal', 'danzhengtong', build_stage_result(
                context, fields, remote_seal.get('requests', []), remote_seal.get('trace'),
                artifact_dir=remote_seal.get('artifact_dir'),
                artifact_url_prefix=remote_seal.get('artifact_url_prefix', ''),
                download_mode=remote_seal.get('download_mode', 'inline'))))
    base = empty_result(context)
    if PROVENANCE_KEY in saved.extra:
        base[PROVENANCE_KEY] = deepcopy(saved.extra[PROVENANCE_KEY])
    output = assemble_result(context, config, results, failures,
                             base, fields, source, None, {
                                 'recompare_saved_checks': bool(remote and not remote_failed),
                                 'preserve_provider_checks': preserve_provider_checks,
                             })
    if remote_failed:
        output['danzhengtong'] = deepcopy(remote)
    if remote_seal is not None and remote_seal.get('preparation_error'):
        output['danzhengtong_seal'] = deepcopy(remote_seal.get('trace') or {})
    if pending:
        output = mark_danzhengtong_pending(output, config)
    # Scope the current policy context to this operation. Reusing saved checks
    # does not relabel their original rules, and a seal-only response cannot
    # claim that the saved date was evaluated under today's comparison policy.
    page_stages = {stage for stage in STAGES if 'danzhengtong' in config[stage]
                   and stage != 'products' and not (stage == 'seal' and remote_seal is not None)}
    fresh_page = bool(page_stages and completed_real_trace(remote)
                      and not inactive(remote.get('result'))
                      and isinstance((remote.get('result') or {}).get('commitResult'), dict))
    fresh_seal = bool(remote_seal is not None and 'danzhengtong' in config['seal']
                      and not remote_seal.get('preparation_error')
                      and completed_real_trace(remote_seal.get('trace'))
                      and any(item.get('status') in {'completed', 'received'}
                              and not inactive(item) and not inactive(item.get('result'))
                              and (item.get('region') or {}).get('role') != '发货单位章'
                              and isinstance(item.get('result'), dict)
                              and isinstance(item['result'].get('commitResult'), dict)
                              for item in remote_seal.get('requests', []) if isinstance(item, dict)))
    comparison_stages = {stage for stage in ('date', 'seal') if fresh_page and config[stage]
                         and (stage in page_stages or not preserve_provider_checks)}
    if fresh_seal:
        comparison_stages.add('seal')
    rules = tuple(f'{stage}_comparison' for stage in ('date', 'seal') if stage in comparison_stages)
    rules += ('evidence_selection', 'overall_decision')
    return record_evaluation(output, 'saved_reassembly', rules,
                             preserve_provider_checks=bool(preserve_provider_checks),
                             new_remote_evidence=fresh_page or fresh_seal)
