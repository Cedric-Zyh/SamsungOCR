"""Prepare one isolated circular mark beside a measured receiving-purpose row.

Geometry uses actual red ink and row spacing only. No requested wording or
target number is accepted, and this module never runs OCR or supplies a digit.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.io import _read_image


def _reject(reason, **metadata):
    return dict(prepared=False, reason=reason, inputs=[], candidates=[], **metadata), []


def _parts(mask):
    height = mask.shape[0]
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    parts = [dict(box=[int(x), int(y), int(x+w), int(y+h)], area=int(area))
             for x, y, w, h, area in stats[1:]
             if height*.25 <= h <= height*.80 and height*.12 <= w <= h*1.65
             and area >= max(24, height*height*.002)]
    # A separately connected digit inside a circular outline belongs to the
    # same physical symbol. It must not count as a second neighbouring mark.
    return sorted((part for part in parts if not any(
        other is not part and other['box'][0] <= part['box'][0]
        and other['box'][1] <= part['box'][1]
        and other['box'][2] >= part['box'][2]
        and other['box'][3] >= part['box'][3]
        and other['area'] > part['area']
        for other in parts)), key=lambda part: part['box'][0])


def _purpose_group(parts, purpose_right):
    if not parts:
        return None
    unit = float(np.median([p['box'][3]-p['box'][1] for p in parts]))
    groups = []
    for part in parts:
        if (not groups or part['box'][0] - max(p['box'][2] for p in groups[-1])
                > max(6, unit*.45)):
            groups.append([part])
        else:
            groups[-1].append(part)
    groups = [group for group in groups if len(group) >= 3
              and max(p['box'][2] for p in group)-min(p['box'][0] for p in group) >= unit*2]
    if purpose_right is not None:
        groups = [group for group in groups
                  if max(p['box'][2] for p in group) <= purpose_right]
    if not groups:
        return None
    return max(groups, key=lambda group: (len(group), max(p['box'][2] for p in group)
                                          - min(p['box'][0] for p in group)))


def _circle_evidence(mask, box):
    left, top, right, bottom = box
    component = mask[top:bottom, left:right]
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if len(contour) < 12:
        return None
    (cx, cy), (a, b), angle = cv2.fitEllipse(contour)
    if min(a, b) <= 0 or not np.isfinite([cx, cy, a, b, angle]).all():
        return None
    points = contour[:, 0, :].astype(float)
    turn = np.deg2rad(angle)
    dx, dy = points[:, 0]-cx, points[:, 1]-cy
    xx = (dx*np.cos(turn)+dy*np.sin(turn))/(a/2)
    yy = (-dx*np.sin(turn)+dy*np.cos(turn))/(b/2)
    residuals = np.abs(np.hypot(xx, yy)-1)
    height, width = component.shape
    ys, xs = np.indices(component.shape)
    nx, ny = (xs-(width-1)/2)/(width/2), (ys-(height-1)/2)/(height/2)
    rho = np.hypot(nx, ny)
    angles = (np.mod(np.arctan2(ny, nx), 2*np.pi)*72/(2*np.pi)).astype(int)
    annulus = (component > 0) & (rho >= .70) & (rho <= 1.10)
    support = np.bincount(angles[annulus], minlength=72)
    core = rho < .45
    evidence = dict(
        method='component_outer_ellipse_and_angular_ink',
        contour_area_fraction=float(cv2.contourArea(contour)/(height*width)),
        ellipse_axes=[float(a), float(b)], ellipse_angle=float(angle),
        ellipse_axis_ratio=float(max(a, b)/min(a, b)),
        median_residual=float(np.median(residuals)),
        p90_residual=float(np.quantile(residuals, .90)),
        outer_angular_coverage=float(np.mean(support >= 2)),
        inner_ink_fraction=float(component[core].mean()) if core.any() else 0.,
    )
    return dict(evidence, accepted=bool(
        .45 <= evidence['contour_area_fraction'] <= .85
        and evidence['ellipse_axis_ratio'] <= 1.30
        and evidence['median_residual'] <= .15 and evidence['p90_residual'] <= .28
        and evidence['outer_angular_coverage'] >= .72
        and .04 <= evidence['inner_ink_fraction'] < .98))


def prepare_receiving_number(row_image_path, directory, index, prefix, *, row_index,
                             purpose_right=None):
    """Return metadata and two real-colour inputs for one right-side mark.

    The caller must supply a complete physical row already identified as the
    receiving purpose. Optional ``purpose_right`` is the observed purpose
    box's right edge in this row image's pixel coordinates, never a fraction
    or a required-text length. Without it, a substantial contiguous ink group
    establishes the body and its actual right edge.

    The crop keeps the complete component plus 8 source pixels on each side.
    The native input adds a 16px white border. The second input scales the
    same source crop by 2 and also adds 16px of white border. Both stay
    diagnostic until the caller verifies actual OCR reads.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in (index, row_index)):
        return _reject('invalid_region_or_row_index')
    try:
        image = _read_image(row_image_path)
    except (OSError, ValueError, TypeError):
        return _reject('unreadable_row_image')
    height, width = image.shape[:2]
    base = dict(source_size=[width, height], row_index=row_index,
                coordinate_space='physical_receiving_row', chroma_threshold=45)
    if height < 32 or width < height*2:
        return _reject('unsupported_row_geometry', **base)
    if purpose_right is not None:
        if isinstance(purpose_right, bool):
            return _reject('invalid_purpose_right', **base)
        try:
            purpose_right = float(purpose_right)
        except (TypeError, ValueError, OverflowError):
            return _reject('invalid_purpose_right', **base)
        if not math.isfinite(purpose_right) or not 0 < purpose_right < width:
            return _reject('invalid_purpose_right', **base)
    blue, green, red = cv2.split(image.astype(np.int16))
    mask = (red-np.maximum(blue, green) > 45).astype(np.uint8)
    parts = _parts(mask)
    body = _purpose_group(parts, purpose_right)
    if body is None:
        return _reject('no_complete_purpose_ink_group', **base)
    body_box = [min(p['box'][0] for p in body), min(p['box'][1] for p in body),
                max(p['box'][2] for p in body), max(p['box'][3] for p in body)]
    unit = float(np.median([p['box'][3]-p['box'][1] for p in body]))
    edge = purpose_right if purpose_right is not None else body_box[2]
    base.update(purpose_box=body_box, purpose_right=float(edge),
                purpose_right_source='detected_box' if purpose_right is not None else 'contiguous_ink_group',
                glyph_height=unit)
    # Inspect every substantial component to the right, not only the first
    # round-looking one; a second mark or unknown glyph keeps the row ambiguous.
    right_parts = [part for part in parts if part not in body and part['box'][2] > edge]
    if len(right_parts) != 1:
        return _reject('no_unique_right_component', right_component_count=len(right_parts), **base)
    box = right_parts[0]['box']
    left, top, right, bottom = box
    w, h = right-left, bottom-top
    gap = left-edge
    candidate = dict(box=box, gap=float(gap), area=right_parts[0]['area'])
    base['candidates'] = [candidate]
    # All dimensions are relative to the observed type glyphs/row, not one
    # sample's x/y fractions. The full 8px sampling margin must remain inside.
    if (gap < max(6, unit*.35) or not .78 <= w/h <= 1.25
            or not .55*unit <= h <= 1.30*unit
            or abs((top+bottom)/2-(body_box[1]+body_box[3])/2) > .30*unit
            or left < 8 or top < 8 or right+8 > width or bottom+8 > height):
        return dict(prepared=False, reason='right_component_geometry_rejected', inputs=[], **base), []
    circle = _circle_evidence(mask, box)
    candidate['circle'] = circle
    if not circle or not circle['accepted']:
        return dict(prepared=False, reason='right_component_not_circular_enclosure', inputs=[], **base), []
    crop_box = [left-8, top-8, right+8, bottom+8]
    x0, y0, x1, y1 = crop_box
    crop = image[y0:y1, x0:x1]
    native = cv2.copyMakeBorder(crop, 16, 16, 16, 16, cv2.BORDER_CONSTANT,
                                value=(255, 255, 255))
    scaled = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    scaled = cv2.copyMakeBorder(scaled, 16, 16, 16, 16, cv2.BORDER_CONSTANT,
                                value=(255, 255, 255))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    inputs, jobs = [], []
    for name, scale, padding, pixels in [('native', 1, 16, native), ('scaled', 2, 16, scaled)]:
        stem = f'receiving-number-{row_index}-{name}'
        path = directory / f'seal-{index}-{stem}.png'
        if not cv2.imwrite(str(path), pixels):
            raise ValueError('收货章右侧圈号输入保存失败')
        item = dict(
            id=f'seal-{index}:{stem}', name=stem, channel='receiving_number',
            label='用途行右侧独立圈号 · 原色' + (' · 2倍加白边' if scale == 2 else ' · 原尺寸'),
            image_url=f'{prefix.rstrip("/")}/{path.name}' if prefix else '',
            ocr_input=True, read_mode='line', matching=False, rectangle_row_index=row_index,
            representation='color', number_scale=scale, blank_padding=padding,
            crop_box=list(crop_box), source_size=[width, height],
            coordinate_space='physical_receiving_row',
        )
        inputs.append(item)
        jobs.append((item, path))
    return dict(prepared=True, reason='unique_spaced_near_circular_right_component',
                crop_box=crop_box, inputs=inputs, **base), jobs
