"""Correct a measured title's bC/DC OCR ambiguity from agreeing real views.

This policy has no requested text. It retains the original decision and uses
the observed detector-crop text only when a separately measured complete grid
maps back to the same physical cells and both grid representations agree.
"""
from copy import deepcopy
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image

from ..preprocess.orientation_geometry import _rotation_geometry
from .receiving_identity import decide_receiving_identity, observation_confidence, _compatible
from .receiving_purpose import real_observation, resolved_purpose_covers

POLICY = 'same_row_title_reread_consensus'
_TITLE = re.compile(r'^([\u4e00-\u9fff]{2,})([bD]C)([0-9][A-Za-z0-9]*)$')


def _literal(value):
    return re.sub(r'\s+', '', str(value or ''))


def _real(value, minimum=.90):
    return real_observation(value) and observation_confidence(value.get('confidence')) >= minimum


def _box(value):
    try:
        box = np.asarray(value, dtype=float)
        if box.shape != (4,) or not np.isfinite(box).all() or box[2] <= box[0] or box[3] <= box[1]:
            return None
        return np.array([[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]])
    except (ValueError, TypeError, OverflowError):
        return None


def _same_physical_cells(artifact, grid, directory):
    """Map grid polygons into the proven primary rotation; never compare spaces directly."""
    frame, orientation = artifact.get('rectangular_frame') or {}, artifact.get('orientation') or {}
    steps = orientation.get('rotation_steps') or []
    if (artifact.get('rectangular_layout') != 'frame_rows' or orientation.get('method') != 'rectangle_rim'
            or (frame.get('direction') or {}).get('selected_rotation') != 0
            or frame.get('measurement_width') != 1000 or len(steps) != 1):
        return None
    try:
        angle = float(steps[0])
        if not np.isfinite(angle) or abs(angle) > 25 or abs(angle - float(orientation['applied_rotation'])) > 1e-6:
            return None
        sizes = {}
        base = str(artifact['original_url']).rsplit('/', 1)[0]
        for key in ('original_url', 'color_isolated_url', 'color_isolated_oriented_url'):
            url = artifact[key]
            if not isinstance(url, str) or url.rsplit('/', 1)[0] != base:
                return None
            with Image.open(Path(directory) / Path(url).name) as image:
                sizes[key] = image.size
        source, color, oriented = [sizes[key] for key in ('original_url', 'color_isolated_url', 'color_isolated_oriented_url')]
        if list(source) != grid.get('source_size') or color != (1000, round(source[1]*1000/source[0])):
            return None
        matrix, width, height = _rotation_geometry(*color, angle)
        if oriented != (width, height):
            return None
        primary_matrix = np.vstack([matrix, [0, 0, 1]]) @ np.diag([color[0]/source[0], color[1]/source[1], 1])
        grid_matrix = np.asarray(grid.get('source_to_rotated_matrix'), dtype=float)
        if grid_matrix.shape != (2, 3) or not np.isfinite(grid_matrix).all():
            return None
        transform = primary_matrix @ np.linalg.inv(np.vstack([grid_matrix, [0, 0, 1]]))
        primary_rows, grid_rows = artifact.get('rectangular_fixed_rows') or [], grid.get('rows') or []
        if len(primary_rows) != len(grid_rows) or len(primary_rows) < 3:
            return None
        overlaps = []
        for index, cell in enumerate(grid_rows):
            if cell.get('index') != index:
                return None
            polygon = _box(cell.get('image_box'))
            if polygon is None:
                return None
            mapped = (transform @ np.c_[polygon, np.ones(4)].T).T[:, :2].astype(np.float32)
            area = abs(cv2.contourArea(mapped))
            eligible = []
            for primary_index, row in enumerate(primary_rows):
                rectangle = _box(row.get('crop_box'))
                if row.get('coordinate_space') != 'oriented' or rectangle is None:
                    return None
                rectangle = rectangle.astype(np.float32)
                other_area = abs(cv2.contourArea(rectangle))
                intersection, _ = cv2.intersectConvexConvex(mapped, rectangle)
                ratios = [intersection / area, intersection / other_area] if min(area, other_area) > 0 else [0, 0]
                if min(ratios) >= .90:
                    eligible.append((primary_index, ratios))
            if len(eligible) != 1 or eligible[0][0] != index:
                return None
            overlaps.append({'row_index': index, 'grid_fraction': eligible[0][1][0],
                             'primary_fraction': eligible[0][1][1]})
        return {'method': 'source_transform_polygon_overlap', 'minimum_overlap': .90,
                'source_size': list(source), 'primary_size': list(oriented), 'rows': overlaps}
    except (KeyError, TypeError, ValueError, OverflowError, OSError, np.linalg.LinAlgError, cv2.error):
        return None


def _bound_read(artifact, *, channel, index, text, url=None, representation=None):
    """Require an actual completed OCR input, not only a derived summary label."""
    region = artifact.get('region_id')
    if not isinstance(region, str) or not region:
        return False
    for item in artifact.get('inputs') or []:
        if (not real_observation(item) or item.get('status') != 'completed' or item.get('read_mode') != 'line'
                or item.get('channel') != channel or item.get('rectangle_row_index') != index
                or isinstance(item.get('rectangle_row_index'), bool)
                or not str(item.get('id') or '').startswith(region + ':')
                or (channel == 'receiving_grid' and item.get('receiving_direction') != 0)
                or (url is not None and item.get('image_url') != url)
                or (representation is not None and item.get('representation') != representation)):
            continue
        values = [v for v in artifact.get('reads') or [] if v.get('input_id') == item['id']]
        if (values and all(_real(v) and v.get('channel') == channel and v.get('region_id') == region
                          and v.get('provider') == 'paddle_v6' and v.get('rectangle_row_index') == index
                          and not isinstance(v.get('rectangle_row_index'), bool)
                          and v.get('image_url') == item.get('image_url') for v in values)
                and _literal(''.join(str(v.get('text') or '') for v in values)) == text):
            return True
    return False


def resolve_receiving_title_read(artifact, primary, rows, detections, grid, *, directory,
                                  old_form, older_observations, selection):
    """Return an adopted comparison copy only for a corroborated literal bC→DC title."""
    conflicts = primary.get('conflicts') or []
    if (selection.get('can_compare') or old_form is not True or primary.get('purpose_row_index') != 1
            or len(conflicts) != 1 or conflicts[0].get('row_index') != 0
            or conflicts[0].get('reason') != 'disagreeing_whole_row_observations'
            or grid.get('accepted') is not True):
        return None
    observations = conflicts[0].get('observations') or []
    supported = [v for v in observations if v.get('source') in
                 {'same_physical_detection_crop', 'detector_same_physical_row'}]
    if (len(supported) != 2 or {v.get('source') for v in supported} !=
            {'same_physical_detection_crop', 'detector_same_physical_row'}
            or not all(_real(v) for v in supported)):
        return None
    text = _literal(supported[0].get('text'))
    parsed = _TITLE.fullmatch(text)
    if not parsed or parsed[2] != 'DC' or any(_literal(v.get('text')) != text for v in supported):
        return None
    old = parsed[1] + 'bC' + parsed[3]
    if ({_literal(v.get('text')) for v in observations} != {old, text}
            or not all(_real(v, .75) for v in observations)
            or not _bound_read(artifact, channel='receiving_detection', index=0, text=text)):
        return None
    proven = [d for d in grid.get('directions') or [] if d.get('form_verified') is True]
    if len(proven) != 1 or proven[0].get('direction') != 0:
        return None
    direction = proven[0]
    decision = direction.get('decision') or {}
    if decision.get('can_compare') is not True or decision.get('purpose_row_index') != 1:
        return None
    physical = direction.get('rows') or []
    if not physical or physical[0].get('row_index') != 0 or physical[0].get('source_row_index') != 0:
        return None
    views = physical[0].get('variants') or []
    if len(views) != 2 or {v.get('source') for v in views} != {'color', 'gray'}:
        return None
    for value in views:
        source = value['source']
        url = (grid.get('rows') or [{}])[0].get(source + '_url')
        if (not _real(value) or _literal(value.get('text')) != text or not url
                or value.get('image_url') != url
                or not _bound_read(artifact, channel='receiving_grid', index=0, text=text,
                                   url=url, representation=source)):
            return None
    geometry = _same_physical_cells(artifact, grid, directory)
    if geometry is None:
        return None
    adjusted_rows = deepcopy(rows)
    for row in adjusted_rows:
        if row.get('row_index') == 0:
            for value in [row, *(row.get('variants') or [])]:
                if _literal(value.get('text')) == old:
                    value['text'] = text
    resolved = decide_receiving_identity(adjusted_rows, detected_boxes=detections, grid_verified=True)
    if not resolved.get('can_compare'):
        return None
    identity = resolved['identity_text']
    selected_rows = {row['row_index']: row for row in resolved['identity_rows']}
    # All other identity rows retain their actual spelling/number, including
    # the primary's ② when a complete-grid OCR omitted it.
    for row in decision.get('identity_rows') or []:
        target = selected_rows.get(row.get('row_index'))
        if target is None or not _compatible(target['text'], row.get('text')):
            return None
    for value in older_observations or []:
        if not _real(value, .75):
            continue
        observed = _literal(value.get('text'))
        index = value.get('rectangle_row_index', value.get('row_index'))
        if index == 0 and observed in {old, text}:
            continue
        if (observed and observed not in _literal(identity)
                and not resolved_purpose_covers(value, resolved, identity)):
            return None
    chosen = {'text': identity, 'can_compare': True, 'source': 'verified_title_reread',
              'reason': 'same_row_title_corrected_by_verified_rereads'}
    return {'selection': chosen, 'resolved_primary': resolved,
            'audit': {'policy': POLICY, 'accepted': True, 'row_index': 0, 'before_texts': [old], 'text': text,
                      'observations': deepcopy(observations), 'grid_observations': deepcopy(views),
                      'geometry': geometry, 'resolved_primary': resolved, 'original_selection': deepcopy(selection)}}
