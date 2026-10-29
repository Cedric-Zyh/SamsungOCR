"""Keep three agreeing same-row rereads when an older read adds a trailing 1.

The original body reading remains evidence. This narrow selection rule does
not infer the character's pixel origin, consult a requested seal, or alter a
purpose/number row. A matching identity still goes through normal comparison.
"""
from copy import deepcopy
import math
import re

from .receiving_purpose import real_observation

POLICY = 'same_row_reread_tail_consensus'
_SOURCES = ('receiving_primary', 'same_physical_detection_crop', 'detector_same_physical_row')
_TITLE = re.compile(r'^[\u4e00-\u9fff]{2,}[A-Z]{1,4}[0-9][0-9A-Z]*[A-Z]$')


def _confidence(value):
    if not real_observation(value) or isinstance(value.get('confidence'), bool):
        return None
    try:
        score = float(value.get('confidence'))
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None


def _literal(value):
    return re.sub(r'\s+', '', str(value or ''))


def resolve_original_row_tail(values, row_index, *, purpose_row_index):
    """Select only an existing complete title supported by all three rereads.

    Call only on one geometrically verified physical row. The original source
    is admitted upstream only after its crop/input/provider provenance agrees.
    An observed purpose or stamp number can never enter this exception.
    """
    if (isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0
            or isinstance(purpose_row_index, bool) or not isinstance(purpose_row_index, int)
            or row_index >= purpose_row_index or len(values or []) != 4
            or any(not isinstance(value, dict) for value in values)):
        return None
    by_source = {value.get('source'): value for value in values}
    if set(by_source) != {*_SOURCES, 'original_physical_row'}:
        return None
    supporting = [by_source[source] for source in _SOURCES]
    scores = [_confidence(value) for value in supporting]
    original = by_source['original_physical_row']
    old_score = _confidence(original)
    if (any(score is None or score < .90 for score in scores)
            or old_score is None or not .75 <= old_score < min(scores)):
        return None
    text = supporting[0].get('text')
    literal = _literal(text)
    if (not isinstance(text, str) or not _TITLE.fullmatch(literal)
            or any(_literal(value.get('text')) != literal for value in supporting)
            or _literal(original.get('text')) != literal + '1'):
        return None
    return {'policy': POLICY, 'accepted': True, 'row_index': row_index, 'text': text,
            'before_texts': [original['text']], 'observations': deepcopy(values),
            'superseded_observations': [deepcopy(original)], 'supporting_sources': list(_SOURCES),
            'minimum_supporting_confidence': min(scores),
            'reason': 'lower_confidence_original_tail_outlier'}
