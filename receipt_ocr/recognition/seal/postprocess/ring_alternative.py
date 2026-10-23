"""Select a complete observed alternative from the same physical stamp."""
from copy import deepcopy
import math

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from .ring_text import reorder_ring_text


def _candidates(ring, artifact):
    centers = [value.strip() for value in str(artifact.get('round_type_band_text') or '').split('|')
               if value.strip()]
    return [ring, *(ring + center for center in centers)]


def select_ring_alternative(artifact, requirement):
    """An actual full match wins over a conflicting higher-confidence reading.

    The requirement selects between observed candidates; it supplies no
    characters. Centre/type/number evidence comes only from this artifact.
    """
    alternative = artifact.get('ring_normal_alternative') or {}
    if not requirement or alternative.get('status') != 'completed':
        return []
    if alternative.get('region_id') not in (None, artifact.get('region_id')):
        return []
    reads = alternative.get('reads') or []
    if not reads or alternative.get('simulated'):
        return []
    try:
        if any(not str(row.get('text') or '').strip()
               or not math.isfinite(float(row.get('confidence', 0)))
               or not .75 <= float(row.get('confidence', 0)) <= 1
               for row in reads):
            return []
    except (TypeError, ValueError):
        return []
    raw = ''.join(str(row['text']) for row in reads).strip()
    if raw != str(alternative.get('text') or '').strip():
        return []
    reorder = reorder_ring_text(raw, requirement=requirement)
    ring = reorder['text']
    candidates = _candidates(ring, artifact)
    comparison = compare_seal_text_strict(requirement, candidates, mismatch_over_partial=True)
    alternative['comparison'] = comparison
    alternative['matching'] = comparison['status'] == '匹配'
    if not alternative['matching']:
        alternative['selected'] = False
        return []

    # Every accepted candidate is a complete ring/type combination from this
    # stamp. Do not add an incomplete alternative that could mask a conflict.
    matching = [text for text in candidates
                if compare_seal_text_strict(requirement, [text])['status'] == '匹配']
    artifact['matching_texts'] = list(dict.fromkeys([*(artifact.get('matching_texts') or []), *matching]))
    primary = str(artifact.get('unwrapped_text') or '')
    primary_check = compare_seal_text_strict(requirement, _candidates(primary, artifact),
                                           mismatch_over_partial=True)
    selected = primary_check['status'] != '匹配'
    alternative['selected'] = selected
    artifact['ring_candidate_selection'] = {
        'policy': 'any_exact_observed_candidate',
        'selected': 'alternative' if selected else 'primary',
        'primary_text': primary, 'alternative_text': ring,
        'primary_status': primary_check['status'], 'alternative_status': comparison['status'],
    }
    if selected:
        artifact['ring_primary'] = {
            'text': primary, 'raw_text': artifact.get('unwrapped_raw_text', primary),
            'image_url': artifact.get('unwrapped_url', ''),
            'ring_reorder': deepcopy(artifact.get('ring_reorder')),
        }
        artifact.update(unwrapped_text=ring, unwrapped_raw_text=raw,
                        unwrapped_url=alternative.get('image_url', ''),
                        unwrap_texts=[ring], ring_reorder=reorder)
    return matching
