"""Read an isolated same-row circled number without a requested value."""
from copy import deepcopy
from pathlib import Path
import math
import re

from ..preprocess.receiving_number import prepare_receiving_number
from ..postprocess.receiving_identity import map_receiving_detections, observation_confidence
from .receiving import read_input


PURPOSE = '收货专用章'
_CIRCLE = re.compile(r'^[①-⑳]$')


def _literal(text):
    return ''.join(chr(ord(ch)-0xFEE0) if '\uff01' <= ch <= '\uff5e' else ch
                   for ch in str(text or '') if not ch.isspace())


def _purpose_right(artifact, index, row_size):
    """Map only an observed bare-purpose detector box to the saved row crop."""
    frame = artifact.get('rectangular_frame') or {}
    prepared = artifact.get('rectangular_fixed_rows') or []
    try:
        l, t, r, b = map(float, frame['crop_box'])
        left, top, right, bottom = map(float, prepared[index]['crop_box'])
        canonical = float(frame['canonical_width'])
        padding = float(frame.get('padding', 32))
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if (not all(math.isfinite(v) for v in [l,t,r,b,left,top,right,bottom,canonical,padding])
            or r <= l or right <= left or canonical <= 0):
        return None
    # Frame rows are saved at original oriented resolution with a 20px rim.
    if row_size != (round(right-left)+40, round(bottom-top)+40):
        return None
    edges = []
    for box in map_receiving_detections(artifact):
        if (box.get('row_index') != index or _literal(box.get('text')) != PURPOSE
                or observation_confidence(box.get('confidence')) < .75):
            continue
        edge = max(float(p[0]) for p in box['points'])
        mapped = l + (edge-padding)*(r-l)/canonical-left+20
        if 0 < mapped < row_size[0]:
            edges.append(mapped)
    return max(edges) if edges else None


def _number_conflict(artifact, index, token):
    values = [r for r in artifact.get('reads') or []
              if r.get('channel') in {'body', 'receiving_primary', 'receiving_detection'}
              and r.get('rectangle_row_index') == index]
    values += [r for r in map_receiving_detections(artifact) if r.get('row_index') == index]
    for value in values:
        if observation_confidence(value.get('confidence')) < .75:
            continue
        text = _literal(value.get('text'))
        if text == token or text == PURPOSE or text == PURPOSE+token:
            continue
        if (text.startswith(PURPOSE) or re.fullmatch(r'收.{1,4}专用章[①-⑳]?', text)
                or _CIRCLE.fullmatch(text)
                or re.fullmatch(r'\(?[0-9]{1,3}\)?', text)):
            return True
    return False


def adopt_receiving_number(artifact, evidence):
    """Project a verified number into this stamp's chosen comparison text."""
    index, token = evidence['row_index'], evidence['number_text']
    inputs, reads = evidence.get('inputs') or [], evidence.get('reads') or []
    if (evidence.get('region_id') != artifact.get('region_id')
            or evidence.get('stamp_index') != artifact.get('index')
            or len(inputs) != 2 or {item.get('number_scale') for item in inputs} != {1, 2}
            or any(item.get('rectangle_row_index') != index
                   or not str(item.get('id') or '').startswith(str(artifact.get('region_id'))+':') for item in inputs)
            or len(reads) != 2 or not _CIRCLE.fullmatch(token)
            or any(_literal(value.get('text')) != token
                   or observation_confidence(value.get('confidence')) < .75 for value in reads)):
        evidence.update(status='unverified', reason='circle_read_provenance_not_verified')
        return None
    if _number_conflict(artifact, index, token):
        evidence.update(status='conflict', reason='existing_identity_number_or_purpose_conflict')
        return None
    form = artifact.get('receiving_form') or {}
    combined_purpose = PURPOSE + token
    if form.get('managed') is True:
        if (form.get('status') != 'adopted' or form.get('selected_source') != 'primary'
                or (form.get('primary') or {}).get('purpose_row_index') != index):
            evidence.update(status='unverified', reason='managed_identity_not_adopted_for_same_row')
            return None
        original = str(form.get('identity_text') or '')
        active = artifact.get('matching_texts') or []
        # A selected managed identity is the only comparable string. Retain
        # that boundary and replace its terminal purpose only, never a name.
        if (not original.endswith(PURPOSE) or original.count(PURPOSE) != 1
                or active != [original]):
            evidence.update(status='unverified', reason='selected_identity_not_bare_purpose')
            return None
        candidate = original[:-len(PURPOSE)] + combined_purpose
        evidence['original_identity_text'] = original
        form['identity_before_receiving_number'] = original
        form['identity_text'] = candidate
        artifact['matching_texts'] = [candidate]
    else:
        parts = artifact.get('rectangular_row_texts') or []
        original = artifact.get('rectangular_text') or ''
        if (not isinstance(parts, list) or index >= len(parts)
                or _literal(parts[index]) != PURPOSE or ''.join(parts) != original
                or sum(_literal(part).startswith(PURPOSE) for part in parts) != 1):
            evidence.update(status='unverified', reason='complete_same_stamp_rows_not_consistent')
            return None
        revised = list(parts)
        revised[index] = str(parts[index]) + token
        candidate = ''.join(revised)
        artifact['rectangular_text_before_receiving_number'] = original
        artifact['rectangular_rows_before_receiving_number'] = list(parts)
        artifact.update(rectangular_text=candidate, rectangular_texts=revised,
                        rectangular_row_texts=revised)
        artifact['matching_texts'] = list(dict.fromkeys([*(artifact.get('matching_texts') or []), candidate]))
        evidence['original_identity_text'] = original
    evidence.update(status='adopted', reason='same_row_two_color_reads_agree',
                    combined_purpose_text=combined_purpose, identity_text=candidate)
    return candidate


def read_receiving_number(artifact, directory, prefix, provider, reader):
    """Read two fixed original-colour crops, then adopt only a literal circle token."""
    if (provider != 'paddle_v6' or artifact.get('shape_code') != 'rectangle'
            or artifact.get('color') != 'red' or artifact.get('rectangular_layout') != 'frame_rows'
            or artifact.get('receiving_number')):
        return None
    prepared = artifact.get('rectangular_fixed_rows') or []
    urls = artifact.get('rectangular_row_urls') or []
    purposes = [r for r in artifact.get('reads') or [] if r.get('channel') == 'body'
                and _literal(r.get('text')) == PURPOSE
                and observation_confidence(r.get('confidence')) >= .75]
    indexes = {r.get('rectangle_row_index') for r in purposes}
    if len(indexes) != 1:
        return None
    index = next(iter(indexes))
    if (not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(prepared)
            or index >= len(urls) or prepared[index].get('coordinate_space') != 'oriented'):
        return None
    source = Path(directory) / Path(str(urls[index])).name
    if not source.is_file():
        return None
    from PIL import Image
    with Image.open(source) as image:
        size = image.size
    metadata, jobs = prepare_receiving_number(
        source, directory, artifact['index'], prefix, row_index=index,
        purpose_right=_purpose_right(artifact, index, size))
    evidence = dict(metadata, status='not_detected', row_index=index,
                    region_id=artifact['region_id'], stamp_index=artifact['index'],
                    original_purpose_texts=list(dict.fromkeys(r['text'] for r in purposes)),
                    policy='same_row_right_circled_number')
    artifact['receiving_number'] = evidence
    if not metadata.get('prepared'):
        return None
    observations = []
    for item, path in jobs:
        # read_input saves all raw observations and counts the actual read.
        observations.append(read_input(artifact, reader, provider, path, item))
    evidence['reads'] = deepcopy(observations)
    tokens = [_literal(value.get('text')) for value in observations]
    if (len(tokens) == 2 and len(set(tokens)) > 1
            and all(_CIRCLE.fullmatch(token) for token in tokens)
            and all(observation_confidence(value.get('confidence')) >= .75 for value in observations)):
        evidence.update(status='conflict', reason='two_reliable_circle_reads_disagree')
        return None
    if (len(tokens) != 2 or len(set(tokens)) != 1
            or not all(_CIRCLE.fullmatch(token) for token in tokens)
            or any(observation_confidence(value.get('confidence')) < .75 for value in observations)):
        evidence.update(status='unverified', reason='two_literal_circle_reads_not_reliable_and_equal')
        return None
    evidence.update(number_text=tokens[0], confidence=min(value['confidence'] for value in observations))
    return adopt_receiving_number(artifact, evidence)
