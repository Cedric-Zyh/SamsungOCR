"""Versioned decision contexts, separate from provider observations and verdicts.

Increment the relevant version when its business rules change. These identifiers
describe an operation's rule context, not which branches ran for each reading;
they do not implement historical rule execution.
Contexts have no clock so read projections and repeated repairs stay idempotent.
"""
from copy import deepcopy
from functools import wraps
import hashlib
import json


RULE_VERSIONS = {
    'date_comparison': '2026-11-11.1',
    'seal_comparison': '2026-11-11.1',
    'evidence_selection': '2026-11-12.2',
    'overall_decision': '2026-11-12.1',
    'seal_standard': '2026-11-11.1',
    'seal_layout': '2026-11-11.1',
    'company_name_correction': '2026-11-11.1',
}
CORE_RULES = ('date_comparison', 'seal_comparison', 'evidence_selection', 'overall_decision')
PROVENANCE_KEY = 'decision_provenance'
_ACCEPTANCE_KEYS = ('date_match_mode', 'seal_match_mode', 'seal_pass_standard', 'signature_match_mode',
                    'reject_mode', 'low_confidence_mode', 'date_source')


def standard_reference(standard):
    """Identify the applied registration, excluding image paths and provider data."""
    if standard is None:
        return {'status': 'missing'}
    return {'status': 'registered', **{key: deepcopy(standard[key]) for key in
        ('id', 'revision', 'requirement_key', 'shape', 'color', 'horizontal_text', 'updated_at') if key in standard}}


def standards_catalog_reference(standards):
    """Fingerprint the frozen vocabulary/layout source without copying it per receipt."""
    values = [{key: item.get(key) for key in ('id', 'revision', 'requirement', 'shape', 'horizontal_text')}
              for item in standards]
    serialized = sorted(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                        for item in values)
    encoded = json.dumps(serialized, ensure_ascii=False, separators=(',', ':')).encode()
    return {'count': len(values), 'sha256': hashlib.sha256(encoded).hexdigest()}


def retained_source_context(document, stage, method):
    """Keep the selected provider's old context without recursively nesting history."""
    provenance = document.get(PROVENANCE_KEY) or {}
    if isinstance(provenance, dict) and provenance.get('schema_version') == 1:
        retained = ((provenance.get('retained_sources') or {}).get(stage) or {}).get(method)
        source = retained if isinstance(retained, dict) else provenance
        return {'schema_version': 1, 'origin': source.get('origin', 'unrecorded'),
                'evaluations': deepcopy(source.get('evaluations') or [])}
    return {'schema_version': 1, 'origin': 'unrecorded', 'evaluations': []}


def attach_retained_sources(snapshot, sources):
    """Attach per-provider contexts to a mixed saved snapshot without inventing versions."""
    if not sources:
        return snapshot
    saved = snapshot.get(PROVENANCE_KEY)
    if saved is not None and (not isinstance(saved, dict) or saved.get('schema_version') != 1):
        return snapshot
    provenance = deepcopy(saved) if saved is not None else {
        'schema_version': 1, 'origin': 'unrecorded', 'evaluations': [],
    }
    provenance['retained_sources'] = deepcopy(sources)
    snapshot[PROVENANCE_KEY] = provenance
    return snapshot


def record_evaluation(result, operation, rules=CORE_RULES, *, origin='unrecorded', **context):
    """Append a changed context without relabeling the origin of saved evidence.

Only the most recent context for the same operation is compared. A later return
to a former policy is a new transition; repeat evaluations under one policy are
not a second audit event. The database's existing history supplies write times.
"""
    saved = result.get(PROVENANCE_KEY)
    if saved is not None and (not isinstance(saved, dict) or saved.get('schema_version') != 1
                              or not isinstance(saved.get('evaluations'), list)):
        return result  # Preserve an unknown future format instead of inventing its history.
    provenance = deepcopy(saved) if saved is not None else {
        'schema_version': 1, 'origin': origin, 'evaluations': [],
    }
    acceptance = (result.get('recognition_config') or {}).get('acceptance') or {}
    evaluation = {
        'operation': operation,
        'scope': 'operation_context',
        'rules': {key: RULE_VERSIONS[key] for key in rules},
        'acceptance': {key: deepcopy(acceptance[key]) for key in _ACCEPTANCE_KEYS if key in acceptance},
        **deepcopy(context),
    }
    previous = next((item for item in reversed(provenance['evaluations'])
                     if isinstance(item, dict) and item.get('operation') == operation), None)
    if previous == evaluation:
        return result
    provenance['evaluations'].append(evaluation)
    result[PROVENANCE_KEY] = provenance
    return result


def track_machine_refresh(operation, rules=CORE_RULES):
    """Record explicit repairs only when they change the saved result.

No-op and human-protected records are returned unchanged, so maintenance does
not write a new revision merely to fill missing historical version information.
"""
    def decorate(refresh):
        @wraps(refresh)
        def tracked(current, *args, **kwargs):
            result = refresh(current, *args, **kwargs)
            if result != current:
                record_evaluation(result, operation, rules)
            return result
        return tracked
    return decorate
