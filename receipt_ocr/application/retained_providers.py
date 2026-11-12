"""Keep saved provider observations when refreshing only one provider."""
from copy import deepcopy

from .deferred_document import empty_snapshot
from .saved_footer import saved_footer_evidence
from .variant_projection import replay_variant
from ..domain.decision_provenance import PROVENANCE_KEY, attach_retained_sources, retained_source_context
from ..domain.stages import STAGES


def merge_artifacts(*sources):
    merged = {}
    for source in sources:
        for key, value in (source or {}).items():
            if isinstance(value, list):
                items = merged.setdefault(key, [])
                for item in value:
                    if item not in items:
                        items.append(deepcopy(item))
            elif key not in merged:
                merged[key] = deepcopy(value)
    return merged


def retained_branch(history, remote_plan, source_hash):
    """Newest observation per provider wins; history entries are newest first."""
    base, snapshot, retained = None, None, {stage: {} for stage in STAGES}
    original_variants = {stage: {} for stage in STAGES}
    retained_sources = {}
    for result, saved in history:
        saved_snapshot = saved.get('snapshot') or {}
        if saved_snapshot.get('source_hash') not in (None, '', source_hash):
            continue
        observations = deepcopy(saved_snapshot.get('results') or {})
        variants = result.get('recognition_variants') or {}
        for stage in STAGES:
            # Public variants also work for results saved before assembly snapshots.
            rendered = []
            for variant in variants.get(stage) or []:
                if variant.get('method') == 'danzhengtong':
                    continue
                # The newest provider outcome owns its slot, including an
                # empty failed/waiting outcome. Dropping that marker would let
                # an older successful reading reappear from another history row.
                rendered.append(replay_variant(stage, variant))
                original_variants[stage].setdefault(variant['method'], deepcopy(variant))
            if rendered:
                observations[stage] = rendered
            if not observations.get(stage):
                methods = [method for method in (result.get('recognition_config') or {}).get(stage, [])
                           if method != 'danzhengtong']
                key = {'fields': 'fields', 'products': 'product_table', 'handwriting': 'handwriting_fields',
                       'date': 'date_check', 'seal': 'seal_check'}[stage]
                if len(methods) == 1 and result.get(key):
                    # Older single-provider results stored observations at the top level.
                    observations[stage] = [{'method': methods[0], 'result': deepcopy(result)}]
        if not any(v.get('method') != 'danzhengtong' for values in observations.values() for v in values):
            continue
        if base is None:
            base = deepcopy(result)
            config = deepcopy(result.get('recognition_config') or saved_snapshot.get('config') or remote_plan)
            snapshot = empty_snapshot(config, result.get('fields'))
            if PROVENANCE_KEY in base:
                snapshot[PROVENANCE_KEY] = deepcopy(base[PROVENANCE_KEY])
        base['processing_artifacts'] = merge_artifacts(base.get('processing_artifacts'), result.get('processing_artifacts'))
        for key in ('raw_text', 'ocr_observations', 'qr_text', 'preview_url', 'processing_timings'):
            if not base.get(key) and result.get(key):
                base[key] = deepcopy(result[key])
        if not snapshot.get('context') or (not snapshot['context'].get('primary_backend') and result.get('ocr_observations')):
            footer = result.get('signature_footer') or {}
            snapshot['context'] = deepcopy(saved_snapshot.get('context') or {
                'document_type': result.get('document_type') or {
                    'type': 'unclassified', 'label': '未执行文档类型识别', 'reliable': False,
                    'confidence': 0, 'reasons': []},
                'has_footer': footer.get('present', False), 'footer_detection': footer,
                'primary_backend': ((result.get('ocr_stage_backends') or {}).get('page') or {}).get('id'),
            })
            snapshot['evidence'] = deepcopy(saved_snapshot.get('evidence') or {
                key: result.get(key) for key in ('ocr_observations', 'qr_text') if result.get(key) is not None
            })
        for stage in STAGES:
            for observation in observations.get(stage) or []:
                method = observation['method']
                if method != 'danzhengtong' and method not in retained[stage]:
                    retained[stage][method] = deepcopy(observation)
                    source = result if PROVENANCE_KEY in result else saved_snapshot
                    retained_sources.setdefault(stage, {})[method] = retained_source_context(source, stage, method)
    if base is None:
        return None
    footer = saved_footer_evidence(snapshot['context'], snapshot['evidence'], base)
    if footer:
        snapshot['context'].update(has_footer=footer['present'], footer_detection=deepcopy(footer))
        snapshot['evidence']['signature_footer'] = deepcopy(footer)
        if not isinstance((base.get('signature_footer') or {}).get('present'), bool):
            base['signature_footer'] = deepcopy(footer)
    for stage in STAGES:
        snapshot['results'][stage] = list(retained[stage].values())
        snapshot['config'][stage] = list(retained[stage])
        if 'danzhengtong' in remote_plan.get(stage, []):
            snapshot['config'][stage].append('danzhengtong')
    snapshot['source_hash'] = source_hash
    snapshot['retained_variants'] = {stage: list(values.values()) for stage, values in original_variants.items()}
    attach_retained_sources(snapshot, retained_sources)
    return {'snapshot': snapshot, 'base': base}


def merge_retained_result(branch, combined):
    result = {**deepcopy(branch['base']), **combined}
    for key in ('raw_text', 'ocr_observations', 'qr_text', 'date_ocr_texts'):
        if key in branch['base']:
            result[key] = deepcopy(branch['base'][key])
    footer = saved_footer_evidence(combined, branch['base'],
                                  branch['snapshot'].get('context'), branch['snapshot'].get('evidence'))
    if footer:
        result['signature_footer'] = footer
    previous_artifacts = deepcopy(branch['base'].get('processing_artifacts') or {})
    current_seals = (combined.get('processing_artifacts') or {}).get('seals')
    for artifact in previous_artifacts.get('seals') or []:
        if not isinstance(artifact, dict):
            continue
        # DZT observations are deliberately rerun, while QingTong observations
        # come from the newest retained variant. Historical images remain
        # inspectable but cannot outvote the replacement text, even at one URL.
        if (artifact.get('provider') == 'danzhengtong'
                or (artifact.get('source') == '清瞳印章区域' and isinstance(current_seals, list)
                    and artifact not in current_seals)):
            artifact.update(execution_status='skipped', skip_reason='superseded_provider_evidence')
    result['processing_artifacts'] = merge_artifacts(previous_artifacts, combined.get('processing_artifacts'))
    for stage, variants in branch['snapshot'].get('retained_variants', {}).items():
        if not variants:
            continue
        incoming = (combined.get('recognition_variants') or {}).get(stage) or []
        result.setdefault('recognition_variants', {})[stage] = (
            deepcopy(variants) + [variant for variant in incoming if variant.get('method') == 'danzhengtong'])
    return result
