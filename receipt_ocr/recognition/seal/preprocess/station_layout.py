"""Three interior rows and two arcs, anchored to the whole oval rim."""
from pathlib import Path

import cv2
import numpy as np

from ..layout_fields import inner_role


def rim_geometry(image):
    blue, green, red = (c.astype(float) for c in cv2.split(image))
    ink = (red-np.maximum(blue, green) > 120).astype('uint8')
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    height, width = image.shape[:2]
    keep = ((stats[:, 4] > height*width*.003)
            & ((stats[:, 2] > width*.35) | (stats[:, 3] > height*.35)))
    keep[0] = False
    yy, xx = np.where(keep[labels])
    if len(xx) < 5:
        return None
    hull = cv2.convexHull(np.column_stack([xx, yy]).astype('float32'))
    if len(hull) < 5:
        return None
    fit = cv2.fitEllipse(hull)
    (cx, cy), (major, minor), angle = fit
    if major < minor:
        major, minor, angle = minor, major, angle-90
    angle = (angle+90) % 180-90
    # This route requires an already upright, substantially complete rim.
    if (not np.isfinite([cx, cy, major, minor, angle]).all()
            or abs(angle) > 15 or not .55 < minor/major < .95
            or not .55*width < major < 1.1*width
            or not .50*height < minor < 1.1*height
            or abs(cx-width/2) > width*.15 or abs(cy-height/2) > height*.15):
        return None
    return fit, (cx, cy, major/2, minor/2, angle)


def station_rows(boxes, offset, shape):
    """Require all three distinct roles; never silently drop an unknown row."""
    height, width = shape[:2]
    rows = []
    for box in boxes:
        points = np.asarray(box.get('points', []), float)
        confidence = float(box.get('confidence') or 0)
        role = inner_role(box.get('text', ''))
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or not np.isfinite(confidence) or confidence < .80
                or role not in {'number', 'type', 'station'}):
            return []
        # Detector boxes describe image edges, so width/height themselves
        # are valid endpoints. Only coordinates beyond those edges are bad.
        if (points.min() < 0 or points[:, 0].max() > width
                or points[:, 1].max() > height):
            return []
        edge = points[1]-points[0]
        if abs(np.rad2deg(np.arctan2(edge[1], edge[0]))) > 15:
            return []
        # Raster masks use inclusive pixel coordinates, unlike edge boxes.
        # Clamp locally before adding the full-image offset.
        points = np.minimum(points, [width-1, height-1])
        points = np.rint(points+offset).astype('int32')
        rows.append({'polygon': points.tolist(), 'role': role,
                     'detected_text': box['text']})
    rows.sort(key=lambda row: min(p[1] for p in row['polygon']))
    if [row['role'] for row in rows] != ['number', 'type', 'station']:
        return []
    for left, right in zip(rows, rows[1:]):
        left_polygon = np.array(left['polygon'], np.float32)
        right_polygon = np.array(right['polygon'], np.float32)
        area, _ = cv2.intersectConvexConvex(
            left_polygon, right_polygon)
        # Detector margins can overlap between tightly stacked rows. Reject
        # overlapping content, not a small shared whitespace margin.
        smaller = min(cv2.contourArea(left_polygon), cv2.contourArea(right_polygon))
        if smaller <= 0 or area > .20*smaller:
            return []
    return rows


def proportional_arc(gray, geometry, degrees, radial, *, lower=False):
    cx, cy, a, b, angle = geometry
    lo, hi = degrees
    inner, outer = radial
    width = round(abs(hi-lo)*np.pi/180*(a+b)/2)
    theta = np.linspace(np.deg2rad(lo), np.deg2rad(hi), width)
    f = np.linspace(inner, outer, 200) if lower else np.linspace(outer, inner, 200)
    x, y = a*np.cos(theta)*f[:, None], b*np.sin(theta)*f[:, None]
    co, si = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
    pixels = cv2.remap(gray, (cx+x*co-y*si).astype('float32'),
                       (cy+x*si+y*co).astype('float32'), cv2.INTER_CUBIC,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return cv2.copyMakeBorder(pixels, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)


def prepare_station_layout(image, directory, index, *, reader, provider):
    fitted = rim_geometry(image)
    if fitted is None:
        return None
    fit, geometry = fitted
    cx, cy, a, b, _ = geometry
    height, width = image.shape[:2]
    left, right = max(0, round(cx-.63*a)), min(width, round(cx+.63*a))
    top, bottom = max(0, round(cy-.60*b)), min(height, round(cy+.62*b))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    def save(name, pixels):
        path = directory / f'seal-{index}-zones-{name}.png'
        if not cv2.imwrite(str(path), pixels):
            raise ValueError('自动分区图片保存失败')
        return path

    central = image[top:bottom, left:right]
    search = save('station-search', central)
    rows = station_rows(reader.detect_boxes(search, provider=provider),
                        np.array([left, top]), central.shape)
    if not rows:
        return None
    overlay, masked = image.copy(), image.copy()
    cv2.ellipse(overlay, fit, (80, 110, 80), 3)
    mask = np.zeros(image.shape[:2], np.uint8)
    plan = {'method': 'ellipse_station_rows', 'rows': [], 'arcs': {}, 'jobs': [],
            'expected_inner_roles': ['number', 'type', 'station'],
            'border': {'method': 'whole_rim_hull', 'ellipse': fit}}
    for i, row in enumerate(rows):
        points = np.array(row['polygon'], np.int32)
        x0, y0 = points.min(axis=0)
        x1, y1 = points.max(axis=0)
        crop = image[max(0, y0-6):min(height, y1+7), max(0, x0-6):min(width, x1+7)]
        crop = cv2.copyMakeBorder(crop, 16, 16, 16, 16, cv2.BORDER_CONSTANT,
                                  value=(255, 255, 255))
        name = f'inner-{i+1}'
        plan['rows'].append({**row, 'name': name})
        plan['jobs'].append((name, save(name, crop), 'layout_inner', f'内部文字 {i+1}'))
        polygon = np.zeros(image.shape[:2], np.uint8)
        cv2.fillPoly(polygon, [points], 255)
        mask[(polygon > 0) & np.any(image < 255, axis=2)] = 255
        cv2.polylines(overlay, [points], True, (80, 110, 80), 3)
    masked[mask > 0] = 255
    plan.update(overlay=save('regions', overlay), ring_input=save('ring-input', masked),
                mask=save('mask', mask))
    blue, green, red = (c.astype(float) for c in cv2.split(masked))
    gray = (255-np.clip((red-np.maximum(blue, green))*255/160, 0, 255)).astype('uint8')
    for name, degrees, radial in [('upper', [-200, 20], [.40, .92]),
                                   ('lower', [155, 25], [.68, .98])]:
        pixels = proportional_arc(gray, geometry, degrees, radial, lower=name == 'lower')
        plan['arcs'][name] = {'degrees': degrees, 'radial_range': radial}
        plan['jobs'].append((name, save(name, pixels),
                             'ring' if name == 'upper' else 'layout_lower',
                             '上弧名称' if name == 'upper' else '下弧电话'))
    return plan
