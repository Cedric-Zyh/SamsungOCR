"""Complete detector rows for oval stamps with separate contact information."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus
from ..layout_fields import inner_role
from .row_extent import save_row_polygon


def fitted_guide(image):
    """A bounded ellipse estimate when scan lines interrupt border tracing."""
    annulus = _fit_ellipse_annulus(_ocr_color_mask(image, 'red'))
    if annulus is None or annulus[0][1] > .06:
        return None
    (cx, cy), (a, b), angle = annulus[0][2]
    if min(a, b) < min(image.shape[:2]) * .55:
        return None
    theta = np.linspace(0, 2*np.pi, 720, endpoint=False)
    t = np.deg2rad(angle)
    points = np.column_stack((
        cx+a/2*np.cos(theta)*np.cos(t)-b/2*np.sin(theta)*np.sin(t),
        cy+a/2*np.cos(theta)*np.sin(t)+b/2*np.sin(theta)*np.cos(t),
    ))
    return SimpleNamespace(
        annulus=annulus, points=points,
        summary=lambda: {'method': 'ellipse_estimate', 'residual': annulus[0][1]},
    )


def detected_rows(boxes, image, rho, *, allow_single_type=False):
    rows = []
    height, width = image.shape[:2]
    for box in boxes:
        points = np.asarray(box.get('points', []), dtype=float)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        if (points[:, 0].min() < 0 or points[:, 1].min() < 0
                or points[:, 0].max() >= width or points[:, 1].max() >= height):
            continue
        x, y = np.rint(points.mean(axis=0)).astype(int)
        row_width = float(np.linalg.norm(points[1]-points[0]))
        row_height = float(np.linalg.norm(points[3]-points[0]))
        angle = np.rad2deg(np.arctan2(*(points[1]-points[0])[::-1]))
        confidence = float(box.get('confidence') or 0)
        role = inner_role(box.get('text', ''))
        if (not np.isfinite(confidence) or confidence < .80 or abs(angle) > 15
                or rho[y, x] > .70 or row_height < 8
                or row_width < (.65 if role == 'number' else 2)*row_height):
            continue
        # A confident but unknown interior row must not silently disappear.
        if not role:
            return []
        rows.append({
            'polygon': points.tolist(), 'role': role,
            'detected_text': str(box.get('text', '')),
            'box': [float(points[:, 0].min()), float(points[:, 1].min()),
                    float(points[:, 0].max()), float(points[:, 1].max())],
        })
    rows.sort(key=lambda row: row['box'][1])
    roles = [row['role'] for row in rows]
    if allow_single_type and roles == ['type']:
        return rows
    if (len(rows) != 2 or roles.count('type') != 1
            or not ({'phone', 'number'} & set(roles))):
        return []
    # Small bounding-box overlap is normal for slightly slanted polygons.
    intersection, _ = cv2.intersectConvexConvex(
        np.asarray(rows[0]['polygon'], np.float32),
        np.asarray(rows[1]['polygon'], np.float32),
    )
    if intersection > 1:
        return []
    return rows


def prepare_detected_layout(source, directory, index, image, guide, rho, *, reader, provider):
    from .inputs import _white_detected_type_region
    from .multi_zone import _arc_maps

    directory = Path(directory)

    def path(name):
        return directory / f'seal-{index}-zones-{name}.png'

    boxes = reader.detect_boxes(source, provider=provider)
    rows = detected_rows(boxes, image, rho, allow_single_type=True)
    if len(rows) == 1:
        # Whole-stamp detection can miss a short parenthesized number.
        # Reuse the established tight-number locator, not a wide middle band.
        from .number import prepare_number_input
        number = prepare_number_input(
            source, path('number-detection'), rows[0]['box'], reader=reader, provider=provider,
        )
        if number.get('status') != 'detected' or inner_role(number.get('text')) != 'number':
            return None
        l, top, r, bottom = number['box']
        boxes = list(boxes) + [{
            'text': number['text'], 'confidence': number['confidence'],
            'points': [[l, top], [r, top], [r, bottom], [l, bottom]],
        }]
        rows = detected_rows(boxes, image, rho)
        if rows:
            rows[-1]['detection_method'] = number.get('method', 'number_detector')
    if not rows:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    _white_detected_type_region(
        Path(source), path('ring-input'),
        polygons=[row['polygon'] for row in rows], mask_destination=path('mask'),
    )
    masked = cv2.imread(str(path('ring-input')))
    maps = _arc_maps(masked, rho, guide, allow_empty_lower=True)
    roles = [row['role'] for row in rows]
    if maps is None or ('number' in roles and 'lower' not in maps):
        return None
    overlay = image.copy()
    plan = {
        'method': 'ellipse_detected_rows', 'rows': [], 'arcs': {}, 'jobs': [],
        'expected_inner_roles': roles, 'border': guide.summary(),
        'overlay': path('regions'), 'ring_input': path('ring-input'), 'mask': path('mask'),
    }
    for i, row in enumerate(rows):
        name = f'inner-{i+1}'
        save_row_polygon(source, path(name), row['polygon'])
        cv2.polylines(overlay, [np.rint(row['polygon']).astype(np.int32)], True, (80, 110, 80), 3)
        plan['rows'].append({**row, 'name': name})
        plan['jobs'].append((name, path(name), 'layout_inner', f'内部文字 {i+1}'))
    cv2.imwrite(str(plan['overlay']), overlay)
    blue, green, red = (c.astype(float) for c in cv2.split(masked))
    gray = (255-np.clip((red-np.maximum(blue, green))*255/160, 0, 255)).astype('uint8')
    for key, mapping in maps.items():
        pixels = cv2.remap(gray, mapping['x'], mapping['y'], cv2.INTER_CUBIC,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        pixels = cv2.copyMakeBorder(pixels, 12, 12, 20, 20, cv2.BORDER_CONSTANT, value=255)
        if not cv2.imwrite(str(path(key)), pixels):
            raise ValueError('自动分区图片保存失败')
        plan['arcs'][key] = {k: mapping[k] for k in ('degrees', 'radial_range')}
        plan['jobs'].append((key, path(key), 'ring' if key == 'upper' else 'layout_lower',
                             '上弧文字' if key == 'upper' else '下弧文字'))
    return plan
