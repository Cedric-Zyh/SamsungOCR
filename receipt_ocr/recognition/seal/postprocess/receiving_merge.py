"""Retain real observations from the same measured receiving-stamp row."""
from copy import deepcopy
import math
import re

from .receiving_identity import observation_confidence
from .receiving_purpose import real_observation


def original_physical_row_observations(artifact, *, provider=None):
    """Return prior body readings only with matching crop, input and stamp provenance.

    The body read and the later 8px-margin reread use the same measured cell.
    Neither a later omission nor a higher confidence makes the earlier text
    disappear. Keep its full literal text so actual disagreements still block.
    """
    if artifact.get('rectangular_layout') != 'frame_rows':
        return {}
    region = artifact.get('region_id')
    prepared, urls = artifact.get('rectangular_fixed_rows') or [], artifact.get('rectangular_row_urls') or []
    if not isinstance(region, str) or not region:
        return {}
    result = {}
    for item in artifact.get('inputs') or []:
        if (not real_observation(item) or item.get('channel') != 'body'
                or item.get('status') != 'completed' or item.get('read_mode') != 'line'):
            continue
        index, input_id = item.get('rectangle_row_index'), item.get('id')
        if (isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < min(len(prepared), len(urls))
                or not isinstance(input_id, str) or not input_id.startswith(region + ':')
                or not urls[index] or item.get('image_url') != urls[index]):
            continue
        crop = prepared[index]
        if not isinstance(crop, dict):
            continue
        try:
            bounds = list(map(float, crop.get('crop_box') or []))
        except (TypeError, ValueError, OverflowError):
            continue
        if (crop.get('coordinate_space') != 'oriented' or len(bounds) != 4
                or not all(math.isfinite(v) for v in bounds)
                or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
            continue
        reads = [read for read in artifact.get('reads') or []
                 if isinstance(read, dict) and read.get('input_id') == input_id]
        if not reads or any(
            not real_observation(read) or read.get('channel') != 'body'
            or read.get('region_id') != region or read.get('rectangle_row_index') != index
            or read.get('image_url') != urls[index]
            or (provider is not None and read.get('provider') != provider)
            or not isinstance(read.get('text'), str) or not read['text'].strip()
            or observation_confidence(read.get('confidence')) < .75
            for read in reads
        ):
            continue
        result.setdefault(index, []).append({
            'text': ''.join(read['text'] for read in reads),
            'confidence': min(observation_confidence(read.get('confidence')) for read in reads),
            'source': 'original_physical_row', 'image_url': urls[index],
            'input_id': input_id, 'read_ids': [read.get('id') for read in reads],
        })
    return result


def complete_receiving_candidate_number(candidate, primary):
    """Complete only a same-stamp display candidate's omitted purpose number.

    This is not an accepted identity: unrelated title/code conflicts continue
    to withhold matching_texts. Existing differing numbers are never replaced.
    """
    purpose = primary.get('purpose_row_index')
    if isinstance(purpose, bool) or not isinstance(purpose, int) or purpose < 0 or not isinstance(candidate, str):
        return None
    if any(c.get('row_index') == purpose for c in primary.get('conflicts') or []):
        return None
    rows = [row for row in primary.get('identity_rows') or [] if row.get('row_index') == purpose]
    if len(rows) != 1:
        return None
    row = rows[0]
    selected = re.sub(r'\s+', '', str(row.get('text') or ''))
    match = re.fullmatch(r'收货专用章([①-⑳]|\([0-9０-９]{1,3}\)|（[0-9０-９]{1,3}）)', selected)
    bare = re.sub(r'\s+', '', candidate)
    if not match or not bare.endswith('收货专用章') or bare.count('收货专用章') != 1:
        return None
    # The selected row must still carry the actual complete numbered reading,
    # or the separately audited same-row 货/费 resolution already authorized.
    observed = [value for value in row.get('observations') or []
                if real_observation(value) and observation_confidence(value.get('confidence')) >= .75]
    resolution = row.get('purpose_resolution') or {}
    supported = any(re.sub(r'\s+', '', str(value.get('text') or '')) == selected for value in observed)
    resolved = (resolution.get('policy') == 'observed_receiving_purpose' and resolution.get('accepted') is True
                and resolution.get('row_index') == purpose and resolution.get('text') == selected)
    if not supported and not resolved:
        return None
    return {'policy': 'same_physical_row_number_retained', 'row_index': purpose,
            'before': candidate, 'text': candidate.rstrip() + match[1],
            'number_text': match[1], 'purpose_text': row['text'],
            'observations': deepcopy(observed)}
