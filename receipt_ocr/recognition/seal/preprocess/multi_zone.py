"""Conservative multi-row ellipse layout; geometry never uses expected text."""
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border
from .company_border import company_border_points


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
        rows.append({'box': [left, top, right, bottom], 'component_boxes': group, 'read_box': [
            max(0, left-pad), max(0, top-pad), min(image.shape[1], right+pad),
            min(image.shape[0], bottom+pad)]})
    return sorted(rows, key=lambda row: row['box'][1])


def _rows_overlap(left, right):
    """Reject shared component bounds, not empty corners of whole-row boxes.

    Components come from the same connected-ink grouping that located the
    rows. Missing component evidence retains the conservative row-box check.
    A stroke joining two rows is grouped before this check, rather than
    being assigned independently to both rows.
    """
    def intersects(a, b):
        return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])

    if not intersects(left['box'], right['box']):
        return False
    left_parts, right_parts = left.get('component_boxes'), right.get('component_boxes')
    if not left_parts or not right_parts:
        return True
    return any(intersects(a, b) for a in left_parts for b in right_parts)


def _continuous_arc_seam(group, cx, cy):
    """Find a single name arc from ink gaps, including its low side glyphs.

    An upright continuous name has one dominant blank at the bottom, with
    ordinary character gaps elsewhere. Separate lower text breaks that blank
    and must retain the two-arc route. No OCR or required wording is used.
    """
    # Connected components are not characters: several bold glyphs may be
    # joined. Let the angular ink/gap evidence decide continuity instead of
    # requiring a particular number of separate blobs.
    if not group:
        return None
    ys = np.concatenate([part[0] for part in group])
    xs = np.concatenate([part[1] for part in group])
    angles = np.mod(np.arctan2(ys - cy, xs - cx), 2 * np.pi)
    bins = np.unique((angles * 720 / (2 * np.pi)).astype(int) % 720)
    if len(bins) < 3:
        return None
    gaps = np.diff(np.r_[bins, bins[0] + 720]) / 2
    index = int(np.argmax(gaps))
    largest = float(gaps[index])
    second = float(np.partition(gaps, -2)[-2])
    middle = float((bins[index] / 2 + largest / 2) % 360)
    if not (largest >= 40 and largest >= 4 * second and second <= 15
            and 45 <= middle <= 135):
        return None
    return np.deg2rad(middle)


def _company_arc_parts(group, rho, cx, cy):
    """Ignore only a small, radially separate remnant inside the bottom gap.

    A number stroke left after masking is not an independent lower text row.
    Estimate the name's inner edge from its upper ink, then require any removed
    component to lie wholly inside that band and occupy a narrow bottom angle.
    Keep all original strokes when those independent geometric checks fail.
    """
    seam = _continuous_arc_seam(group, cx, cy)
    if seam is not None or not group:
        return group, seam
    ys = np.concatenate([part[0] for part in group])
    xs = np.concatenate([part[1] for part in group])
    upper = ys < cy
    if not np.any(upper):
        return group, None
    inner = float(np.quantile(rho[ys[upper], xs[upper]], .20))
    kept, remnants = [], []
    for yy, xx in group:
        theta = np.rad2deg(np.arctan2(yy-cy, xx-cx))
        if (np.quantile(rho[yy, xx], .99) < inner
                and np.all((theta >= 60) & (theta <= 120))):
            remnants.append((yy, xx))
        else:
            kept.append((yy, xx))
    if not remnants or len(remnants) > 2:
        return group, None
    ry = np.concatenate([part[0] for part in remnants])
    rx = np.concatenate([part[1] for part in remnants])
    angles = np.rad2deg(np.arctan2(ry-cy, rx-cx))
    if len(ry) > .05*len(ys) or np.ptp(angles) > 15:
        return group, None
    seam = _continuous_arc_seam(kept, cx, cy)
    return (kept, seam) if seam is not None else (group, None)


def _arc_maps(masked, rho, guide, *, allow_empty_lower=False, allow_sparse_lower=False,
              allow_continuous_name=False):
    (cx, cy), (a, b), _ = guide.annulus[0][2]
    ink = ((255-masked[:, :, 1].astype(float)) > 35) & (rho < .93)
    _, labels, stats, centers = cv2.connectedComponentsWithStats(
        cv2.morphologyEx(ink.astype('uint8'), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), 8)
    parts = {'upper': [], 'lower': []}
    continuous_parts = []
    for i, (_, _, w, h, area) in enumerate(stats[1:], 1):
        if area < min(a, b)**2*.0002 or max(h, w) < min(a, b)*.035:
            continue
        ys, xs = np.where(labels == i)
        radial = float(np.median(rho[ys, xs]))
        if not .48 < radial < .88:
            continue
        # The size cap helps locate separated rows, but would erase several
        # joined company glyphs before the continuous arc bounds are measured.
        # Keep those actual annular strokes for the continuous-name decision;
        # retain the established cap if the image needs the two-arc route.
        continuous_parts.append((ys, xs))
        if max(w, h) > max(a, b)*.30:
            continue
        key = 'lower' if centers[i, 1] > cy+min(a, b)*.15 else 'upper'
        parts[key].append((ys, xs))
    seam = None
    if allow_continuous_name:
        continuous_parts, seam = _company_arc_parts(continuous_parts, rho, cx, cy)
        if seam is not None:
            parts = {'company': continuous_parts}
    points, border_adjustment = (company_border_points(guide) if 'company' in parts
                                 else (guide.points, None))
    if border_adjustment is not None:
        (cx, cy), (a, b), angle = border_adjustment['ellipse']
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
        if len(group) < 3 and key != 'company':
            # Optional lower arc on a verified phone + purpose layout: isolated
            # remnants do not invalidate its complete upper company-name arc.
            if key == 'lower' and allow_sparse_lower:
                continue
            return None
        ys = np.concatenate([p[0] for p in group])
        xs = np.concatenate([p[1] for p in group])
        theta = np.arctan2(ys-cy, xs-cx)
        if key == 'company':
            theta = np.mod(theta - seam, 2 * np.pi) + seam
            # Preserve every observed end stroke; the margin lies inside
            # the measured blank rather than cutting a glyph by its height.
            lo, hi = float(theta.min() - .035), float(theta.max() + .035)
        else:
            theta = (np.mod(theta-np.pi/2, 2*np.pi)+np.pi/2 if key == 'upper'
                     else np.mod(theta, 2*np.pi))
            lo, hi = float(np.quantile(theta, .002)-.035), float(np.quantile(theta, .998)+.035)
        span = np.rad2deg(hi-lo)
        minimum, maximum = (45, 160) if key == 'lower' else (100, 300 if key == 'company' else 240)
        if not minimum <= span <= maximum:
            return None
        start, end = (hi, lo) if key == 'lower' else (lo, hi)
        radii = rho[ys, xs]
        if border_adjustment is not None:
            turn = np.deg2rad(angle)
            dx, dy = xs - cx, ys - cy
            radii = np.hypot((dx * np.cos(turn) + dy * np.sin(turn)) / (a / 2),
                             (-dx * np.sin(turn) + dy * np.cos(turn)) / (b / 2))
        inner = max(.38, float(np.quantile(radii, 0 if key == 'company' else .005))-.035)
        outer = min(.96, float(np.quantile(radii, 1 if key == 'company' else .995))+.035)
        if outer-inner < .12:
            return None
        theta = np.mod(np.linspace(start, end, 2400), 2*np.pi)
        bx, by = np.interp(theta, aa, xx), np.interp(theta, aa, yy)
        distance = np.r_[0, np.cumsum(np.hypot(np.diff(bx), np.diff(by)))]
        samples = np.linspace(0, distance[-1], round(distance[-1]))
        bx, by = np.interp(samples, distance, bx), np.interp(samples, distance, by)
        radial = np.linspace(inner, outer, 200) if key == 'lower' else np.linspace(outer, inner, 200)
        maps[key] = {
            'x': (cx+(bx-cx)*radial[:, None]).astype('float32'),
            'y': (cy+(by-cy)*radial[:, None]).astype('float32'),
            'degrees': [float(np.rad2deg(start)), float(np.rad2deg(end))],
            'radial_range': [inner, outer],
        }
        if border_adjustment is not None:
            maps[key]['border_adjustment'] = border_adjustment
    return maps


def prepare_multi_zone(source, directory, index, *, reader=None, provider=None, color='red', ring_guide=None,
                       orientation=None, layout_evidence=None):
    """Separate contact layouts, retaining the established multi-row route.

    Prefer complete detector rows over fragmented component groups. Keep the
    component route for other multi-row layouts; ordinary one-row stamps fall back.
    Required wording never chooses geometry.
    """
    image = cv2.imread(str(source))
    if image is None:
        return None
    if reader is not None:
        from .station_layout import prepare_station_layout
        station_plan = prepare_station_layout(image, directory, index, reader=reader,
                                              provider=provider, color=color)
        if station_plan is not None:
            return station_plan
    guide = None
    if ring_guide is not None and ring_guide.source_shape == image.shape[:2]:
        from .detected_layout import fitted_guide
        guide = fitted_guide(image, color=color, geometry=ring_guide.annulus)
    if guide is None:
        guide = trace_ellipse_border(source, color=color)
    if guide is None:
        from .detected_layout import fitted_guide
        # The same bounded fit is also usable by the component-row route.
        # Broken table-overlapped rims should not discard otherwise separate
        # rows; fitted_guide still enforces its size and residual limits.
        guide = fitted_guide(image, color=color)
    if guide is None:
        return None
    (cx, cy), (a, b), angle = guide.annulus[0][2]
    yy, xx = np.indices(image.shape[:2])
    t = np.deg2rad(angle)
    dx, dy = xx-cx, yy-cy
    rho = np.hypot((dx*np.cos(t)+dy*np.sin(t))/(a/2),
                   (-dx*np.sin(t)+dy*np.cos(t))/(b/2))
    rows = _inner_rows(image, rho, (a, b))
    if reader is not None:
        from .detected_layout import prepare_detected_layout
        detector_evidence = {}
        detected = prepare_detected_layout(
            source, directory, index, image, guide, rho, reader=reader,
            provider=provider, color=color, evidence=detector_evidence,
            verified_rows=(orientation or {}).get('department_verified_rows'),
        )
        if detected is not None:
            return detected
        from .single_row_logo import single_row_logo_evidence
        ordinary = single_row_logo_evidence(image, rho, rows, detector_evidence, orientation)
        if ordinary is not None:
            if layout_evidence is not None:
                layout_evidence.update(ordinary)
            return None
    if not 3 <= len(rows) <= 6:
        return None
    # Tilted rows can have intersecting outer boxes even though their actual
    # components sit in different columns. Check every pair, including rows
    # separated by a short number row, before allowing the masking operation.
    if any(_rows_overlap(left, right)
           for i, left in enumerate(rows) for right in rows[i+1:]):
        return None
    masked, overlay = image.copy(), image.copy()
    mask = np.zeros(image.shape[:2], np.uint8)
    for row in rows:
        l, top, r, bottom = row['box']
        if np.max(rho[top:bottom, l:r]) >= .85:
            return None
        mask[top:bottom, l:r] = np.any(image[top:bottom, l:r] < 255, axis=2).astype('uint8')*255
        masked[mask > 0] = 255
        cv2.rectangle(overlay, (l, top), (r, bottom), (80, 110, 80), 3)
    maps = _arc_maps(masked, rho, guide)
    if maps is None:
        return None
    if reader is not None:
        from .single_row_logo import verified_single_type_evidence
        ordinary = verified_single_type_evidence(
            source, directory, index, image, orientation, reader=reader, provider=provider)
        if ordinary is not None:
            if layout_evidence is not None:
                layout_evidence.update(ordinary)
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
    chroma = red-np.maximum(blue, green) if color == 'red' else blue-np.maximum(red, green)
    gray = (255-np.clip(chroma*255/160, 0, 255)).astype('uint8')
    for key, mapping in maps.items():
        out = cv2.remap(gray, mapping['x'], mapping['y'], cv2.INTER_CUBIC,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        out = cv2.copyMakeBorder(out, 12, 12, 20, 20, cv2.BORDER_CONSTANT, value=255)
        path = save(key, out)
        plan['arcs'][key] = {k: mapping[k] for k in ('degrees', 'radial_range')}
        plan['jobs'].append((key, path, 'ring' if key == 'upper' else 'layout_lower',
                             '上弧文字' if key == 'upper' else '下弧文字'))
    return plan
