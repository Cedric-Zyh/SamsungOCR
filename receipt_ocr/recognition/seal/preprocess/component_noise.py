"""Reclassify fragmented, unreadable component rows using their own pixels."""
from copy import deepcopy
from pathlib import Path
import re

import cv2
import numpy as np

from ..layout_fields import inner_role


def _texture_evidence(image, box, color):
    left, top, right, bottom = box
    if not (0 <= left < right <= image.shape[1] and 0 <= top < bottom <= image.shape[0]):
        return None
    crop = image[top:bottom, left:right].astype(float)
    blue, green, red = cv2.split(crop)
    chroma = red - np.maximum(blue, green) if color == 'red' else blue - np.maximum(red, green)
    ink = (chroma > 35).astype('uint8')
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    stats = stats[1:]
    area = int(ink.sum())
    if not area:
        return None
    height = bottom - top
    evidence = dict(component_count=len(stats),
                    fragment_fraction=float(stats[stats[:, 3] < height * .4, 4].sum() / area),
                    tall_component_count=int(np.count_nonzero(stats[:, 3] >= height * .6)))
    if (evidence['component_count'] < 12 or evidence['fragment_fraction'] < .4
            or evidence['tall_component_count'] >= 2):
        return None
    return evidence


def component_noise_candidate(artifact, directory, index, prefix):
    """Return a separate two-row candidate; never change the saved raw input.

    Only completed component layouts with one reliable purpose and number
    qualify. Unknown Chinese/numeric or confident text always keeps its row.
    The caller commits this candidate only after continuous OCR succeeds.
    """
    layout = artifact.get('multi_zone_layout') or {}
    if (layout.get('method') != 'ellipse_components'
            or artifact.get('shape_code') not in {'round', 'ellipse'}
            or set(layout.get('arcs', {})) != {'upper', 'lower'}):
        return None
    directory = Path(directory)
    source_name = artifact.get('ring_input_source_name') or Path(
        artifact.get('ring_input_source_url') or '').name
    image = cv2.imread(str(directory / Path(source_name).name)) if source_name else None
    if image is None:
        return None
    kept, discarded = [], []
    for row in layout.get('rows', []):
        inputs = [item for item in artifact.get('inputs', [])
                  if item.get('channel') == 'layout_inner' and item.get('zone_name') == row.get('name')]
        if len(inputs) != 1 or inputs[0].get('status') not in {'completed', 'empty'}:
            return None
        entry = inputs[0]
        reads = [read for read in artifact.get('reads', []) if read.get('input_id') == entry['id']]
        if len(reads) > 1 or not reads and entry.get('status') != 'empty':
            return None
        text = str(reads[0].get('text') or '') if reads else ''
        confidence = float(reads[0].get('confidence') or 0) if reads else 0
        role = inner_role(text, number_field=True)
        if role in {'type', 'number'} and confidence >= .8:
            kept.append({**row, 'role': role})
            continue
        # A failed recognizer is not evidence of noise. Even a faint digit,
        # contact or Chinese row is retained for inspection rather than erased.
        if (confidence >= .6 or re.search(r'[\u3400-\u9fff\d]', text)
                or role or entry.get('status') == 'empty' and reads):
            return None
        box = row.get('box', [])
        if len(box) != 4 or any(type(value) is not int for value in box):
            return None
        texture = _texture_evidence(image, box, artifact.get('color', 'red'))
        if texture is None:
            return None
        discarded.append(dict(name=row['name'], input_id=entry['id'], text=text,
                              confidence=confidence, reason='low_confidence_fragmented_ink', **texture))
    if [row['role'] for row in kept] != ['type', 'number'] or not discarded:
        return None

    candidate = deepcopy(artifact)
    mask = np.zeros(image.shape[:2], np.uint8)
    overlay = image.copy()
    for row in kept:
        left, top, right, bottom = row['box']
        if not (0 <= left < right <= image.shape[1] and 0 <= top < bottom <= image.shape[0]):
            return None
        mask[top:bottom, left:right] = np.any(image[top:bottom, left:right] < 255, axis=2).astype('uint8') * 255
        cv2.rectangle(overlay, (left, top), (right, bottom), (80, 110, 80), 3)
    masked = image.copy()
    masked[mask > 0] = 255
    outputs = {}
    for name, pixels in [('regions', overlay), ('ring-input', masked), ('mask', mask)]:
        path = directory / f'seal-{index}-zones-filtered-{name}.png'
        if not cv2.imwrite(str(path), pixels):
            raise ValueError('剔除杂纹后的分区图片保存失败')
        outputs[name] = f'{prefix.rstrip("/")}/{path.name}' if prefix else ''
    candidate['component_row_filter'] = dict(status='adopted', discarded_rows=discarded,
                                            original_layout=deepcopy(layout))
    candidate['multi_zone_layout'].update(method='ellipse_detected_rows', rows=kept,
        expected_inner_roles=['type', 'number'], overlay_url=outputs['regions'])
    candidate.update(ring_input_url=outputs['ring-input'], ring_input_mask_url=outputs['mask'],
                     ring_input_name=f'seal-{index}-zones-filtered-ring-input.png')
    rejected = {row['input_id'] for row in discarded}
    successor = f'{artifact["region_id"]}:zones-company-continuous'
    for item in [*candidate.get('inputs', []), *candidate.get('reads', [])]:
        if item.get('id') in rejected or item.get('input_id') in rejected:
            item.update(original_channel=item.get('channel'), channel='layout_noise_diagnostic',
                        matching=False, superseded_by=successor, zone_label='杂纹候选（仅供核对）')
    for item in candidate.get('inputs', []):
        name = item.get('name')
        if name in {'zones-regions', 'zones-ring-input', 'zones-mask'}:
            item['image_url'] = outputs[name.removeprefix('zones-')]
    return candidate
