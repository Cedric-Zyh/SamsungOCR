"""Conservative multi-row ellipse layout; geometry never uses expected text."""
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border


def _inner_rows(image, rho, axes):
    a, b = axes
    ink = ((255 - image[:, :, 1].astype(float)) > 35) & (rho < .95)
    closed = cv2.morphologyEx(ink.astype('uint8'), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    components = []
    for index, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if h < min(a, b) * .045 or area < min(a, b)**2 * .00015:
            continue
        if float(np.median(rho[labels == index])) < .60:
            components.append([int(x), int(y), int(x+w), int(y+h)])
    parent = list(range(len(components)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i, (x1, y1, x2, y2) in enumerate(components):
        h = y2-y1
        for j, (left, top, right, bottom) in enumerate(components[:i]):
            hh = bottom-top
            overlap = min(y2, bottom)-max(y1, top)
            gap = max(0, max(x1, left)-min(x2, right))
            if (overlap >= .45*min(h, hh) and gap <= 1.5*max(h, hh)
                    and abs((y1+y2-top-bottom)/2) <= .45*max(h, hh)):
                parent[root(i)] = root(j)
    groups = {}
    for i, box in enumerate(components):
        groups.setdefault(root(i), []).append(box)
    rows = []
    for group in groups.values():
        if len(group) < 2:
            continue
        boxes = np.array(group)
        left, top = int(boxes[:, 0].min()), int(boxes[:, 1].min())
        right, bottom = int(boxes[:, 2].max()), int(boxes[:, 3].max())
        if right-left < .8*(bottom-top):
            continue
        pad = max(5, round((bottom-top)*.08))
        rows.append({'box': [left, top, right, bottom], 'read_box': [
            max(0, left-pad), max(0, top-pad), min(image.shape[1], right+pad),
            min(image.shape[0], bottom+pad)]})
    return sorted(rows, key=lambda row: row['box'][1])


def _arc_maps(masked, rho, guide, *, allow_empty_lower=False):
    (cx, cy), (a, b), _ = guide.annulus[0][2]
    ink = ((255-masked[:, :, 1].astype(float)) > 35) & (rho < .93)
    _, labels, stats, centers = cv2.connectedComponentsWithStats(
        cv2.morphologyEx(ink.astype('uint8'), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), 8)
    parts = {'upper': [], 'lower': []}
    for i, (_, _, w, h, area) in enumerate(stats[1:], 1):
        if area < min(a, b)**2*.0002 or max(h, w) < min(a, b)*.035:
            continue
        ys, xs = np.where(labels == i)
        radial = float(np.median(rho[ys, xs]))
        if not .48 < radial < .88 or max(w, h) > max(a, b)*.30:
            continue
        key = 'lower' if centers[i, 1] > cy+min(a, b)*.15 else 'upper'
        parts[key].append((ys, xs))
    points = guide.points
    angles = np.mod(np.arctan2(points[:, 1]-cy, points[:, 0]-cx), 2*np.pi)
    order = np.argsort(angles)
    angles, points = angles[order], points[order]
    aa = np.r_[angles[-1]-2*np.pi, angles, angles[0]+2*np.pi]
    xx = np.r_[points[-1, 0], points[:, 0], points[0, 0]]
    yy = np.r_[points[-1, 1], points[:, 1], points[0, 1]]
    maps = {}
    for key, group in parts.items():
        if key == 'lower' and not group and allow_empty_lower:
            continue
        if len(group) < 3:
            return None
        ys = np.concatenate([p[0] for p in group])
        xs = np.concatenate([p[1] for p in group])
        theta = np.arctan2(ys-cy, xs-cx)
        theta = (np.mod(theta-np.pi/2, 2*np.pi)+np.pi/2 if key == 'upper'
                 else np.mod(theta, 2*np.pi))
        lo, hi = float(np.quantile(theta, .002)-.035), float(np.quantile(theta, .998)+.035)
        # This branch represents separated upper/lower arcs, not a continuous name.
        span = np.rad2deg(hi-lo)
        if not (100 <= span <= 240 if key == 'upper' else 45 <= span <= 160):
            return None
        start, end = (lo, hi) if key == 'upper' else (hi, lo)
        inner = max(.38, float(np.quantile(rho[ys, xs], .005))-.035)
        outer = min(.96, float(np.quantile(rho[ys, xs], .995))+.035)
        if outer-inner < .12:
            return None
        theta = np.mod(np.linspace(start, end, 2400), 2*np.pi)
        bx, by = np.interp(theta, aa, xx), np.interp(theta, aa, yy)
        distance = np.r_[0, np.cumsum(np.hypot(np.diff(bx), np.diff(by)))]
        samples = np.linspace(0, distance[-1], round(distance[-1]))
        bx, by = np.interp(samples, distance, bx), np.interp(samples, distance, by)
        radial = np.linspace(outer, inner, 200) if key == 'upper' else np.linspace(inner, outer, 200)
        maps[key] = {
            'x': (cx+(bx-cx)*radial[:, None]).astype('float32'),
            'y': (cy+(by-cy)*radial[:, None]).astype('float32'),
            'degrees': [float(np.rad2deg(start)), float(np.rad2deg(end))],
            'radial_range': [inner, outer],
        }
    return maps


def prepare_multi_zone(source, directory, index, *, reader=None, provider=None):
    """Separate contact layouts, retaining the established multi-row route.

    Two interior seeds require verified whole-image rows; three or more use
    the measured-border component route. Ordinary one-row stamps fall back.
    Required wording never chooses geometry.
    """
    image = cv2.imread(str(source))
    if image is None:
        return None
    guide = trace_ellipse_border(source, color='red')
    measured = guide is not None
    if guide is None and reader is not None:
        from .detected_layout import fitted_guide
        guide = fitted_guide(image)
    if guide is None:
        return None
    (cx, cy), (a, b), angle = guide.annulus[0][2]
    yy, xx = np.indices(image.shape[:2])
    t = np.deg2rad(angle)
    dx, dy = xx-cx, yy-cy
    rho = np.hypot((dx*np.cos(t)+dy*np.sin(t))/(a/2),
                   (-dx*np.sin(t)+dy*np.cos(t))/(b/2))
    rows = _inner_rows(image, rho, (a, b))
    if len(rows) == 2 and reader is not None:
        from .detected_layout import prepare_detected_layout
        return prepare_detected_layout(
            source, directory, index, image, guide, rho, reader=reader, provider=provider,
        )
    if not measured:
        return None
    if not 3 <= len(rows) <= 6:
        return None
    # Ambiguous overlapping rows must not erase one another or ring glyphs.
    if any(left['read_box'][3] > right['read_box'][1]
           for left, right in zip(rows, rows[1:])):
        return None
    masked, overlay = image.copy(), image.copy()
    mask = np.zeros(image.shape[:2], np.uint8)
    for row in rows:
        l, top, r, bottom = row['read_box']
        if np.max(rho[top:bottom, l:r]) >= .85:
            return None
        mask[top:bottom, l:r] = np.any(image[top:bottom, l:r] < 255, axis=2).astype('uint8')*255
        masked[mask > 0] = 255
        cv2.rectangle(overlay, (l, top), (r, bottom), (80, 110, 80), 3)
    maps = _arc_maps(masked, rho, guide)
    if maps is None:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    def save(name, pixels):
        path = directory / f'seal-{index}-zones-{name}.png'
        if not cv2.imwrite(str(path), pixels):
            raise ValueError('自动分区图片保存失败')
        return path

    plan = {'method': 'ellipse_components', 'rows': [], 'arcs': {}, 'jobs': [],
            'overlay': save('regions', overlay), 'ring_input': save('ring-input', masked),
            'mask': save('mask', mask), 'border': guide.summary()}
    for i, row in enumerate(rows):
        name = f'inner-{i+1}'
        l, top, r, bottom = row['read_box']
        path = save(name, image[top:bottom, l:r])
        plan['rows'].append({**row, 'name': name})
        plan['jobs'].append((name, path, 'layout_inner', f'内部文字 {i+1}'))
    blue, green, red = (c.astype(float) for c in cv2.split(masked))
    gray = (255-np.clip((red-np.maximum(blue, green))*255/160, 0, 255)).astype('uint8')
    for key, mapping in maps.items():
        out = cv2.remap(gray, mapping['x'], mapping['y'], cv2.INTER_CUBIC,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        out = cv2.copyMakeBorder(out, 12, 12, 20, 20, cv2.BORDER_CONSTANT, value=255)
        path = save(key, out)
        plan['arcs'][key] = {k: mapping[k] for k in ('degrees', 'radial_range')}
        plan['jobs'].append((key, path, 'ring' if key == 'upper' else 'layout_lower',
                             '上弧文字' if key == 'upper' else '下弧文字'))
    return plan
