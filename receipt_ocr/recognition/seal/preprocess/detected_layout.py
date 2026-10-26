"""Complete detector rows for oval stamps with separate contact information."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus, _ellipse_proportional_maps
from ..layout_fields import inner_role
from .row_extent import save_row_polygon


def fitted_guide(image, *, color='red', geometry=None):
    """A bounded ellipse estimate when scan lines interrupt border tracing."""
    annulus = geometry if geometry is not None else _fit_ellipse_annulus(_ocr_color_mask(image, color))
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
    map_x, map_y = _ellipse_proportional_maps(*annulus)
    return SimpleNamespace(
        annulus=annulus, points=points, source_shape=image.shape[:2],
        map_x=map_x, map_y=map_y,
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
        role = inner_role(box.get('text', ''), number_field=box.get('number_field', False))
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
            'detected_confidence': confidence,
            'box': [float(points[:, 0].min()), float(points[:, 1].min()),
                    float(points[:, 0].max()), float(points[:, 1].max())],
        })
    rows.sort(key=lambda row: row['box'][1])
    roles = [row['role'] for row in rows]
    if allow_single_type and roles == ['type']:
        return rows
    if (len(rows) != 2 or roles.count('type') != 1
            or not ({'phone', 'number', 'department'} & set(roles))):
        return []
    if 'department' in roles and roles != ['department', 'type']:
        return []
    # Small bounding-box overlap is normal for slightly slanted polygons.
    intersection, _ = cv2.intersectConvexConvex(
        np.asarray(rows[0]['polygon'], np.float32),
        np.asarray(rows[1]['polygon'], np.float32),
    )
    smaller_area = min(cv2.contourArea(np.asarray(row['polygon'], np.float32))
                       for row in rows)
    if intersection > .12 * smaller_area:
        return []
    return rows


def _detect_layout_rows(source, directory, index, image, rho, *, reader, provider):
    """Detect at a bounded scale, then map the complete rows to source pixels."""
    height, width = image.shape[:2]
    preview_rows = []
    if max(height, width) > 512:
        directory.mkdir(parents=True, exist_ok=True)
        scale = 512 / max(height, width)
        size = (round(width * scale), round(height * scale))
        preview = directory / f'seal-{index}-zones-detection.png'
        if not cv2.imwrite(str(preview), cv2.resize(image, size, interpolation=cv2.INTER_AREA)):
            raise ValueError('文字分区检测图保存失败')
        boxes = []
        for box in reader.detect_boxes(preview, provider=provider):
            points = np.asarray(box.get('points', []), dtype=float)
            if points.shape != (4, 2) or not np.isfinite(points).all():
                continue
            boxes.append({**box, 'points': (points * [width / size[0],
                                                    height / size[1]]).tolist()})
        rows = detected_rows(boxes, image, rho, allow_single_type=True)
        if len(rows) == 2:
            return boxes, rows
        preview_rows = rows
    # Retain fine-detail rows that the smaller preview cannot locate.
    boxes = reader.detect_boxes(source, provider=provider)
    rows = detected_rows(boxes, image, rho, allow_single_type=True)
    if len(rows) == 2 or not preview_rows:
        return boxes, rows
    # A smaller view can detect the complete type row while the native view
    # sees a short department row and splits that same type into glyphs.
    # Retain the observed full polygon and discard only fragments geometrically
    # covered by it, before applying the usual role/overlap checks to the union.
    seeds = [{'text': row['detected_text'], 'confidence': row['detected_confidence'],
              'points': row['polygon']} for row in preview_rows]
    merged = list(seeds)
    for box in boxes:
        polygon = np.asarray(box.get('points', []), np.float32)
        if polygon.shape != (4, 2) or not np.isfinite(polygon).all():
            continue
        covered = False
        for seed in seeds:
            boundary = np.asarray(seed['points'], np.float32)
            area, _ = cv2.intersectConvexConvex(polygon, boundary)
            smaller = min(cv2.contourArea(polygon), cv2.contourArea(boundary))
            if smaller > 0 and area >= .60 * smaller:
                covered = True
                break
        if not covered:
            merged.append(box)
    combined = detected_rows(merged, image, rho, allow_single_type=True)
    return (merged, combined) if len(combined) == 2 else (boxes, rows)


def prepare_detected_layout(source, directory, index, image, guide, rho, *, reader, provider, color='red', evidence=None,
                            verified_rows=None):
    from .inputs import _white_detected_type_region
    from .multi_zone import _arc_maps

    directory = Path(directory)

    def path(name):
        return directory / f'seal-{index}-zones-{name}.png'

    boxes, rows = [], []
    if (verified_rows and verified_rows.get('coordinate_frame') == 'final_oriented_stamp'
            and verified_rows.get('image_size') == [image.shape[1], image.shape[0]]):
        # Reuse the complete rows verified during orientation. A fresh smaller
        # detection must not replace them with fragmented glyph boxes.
        from .department_orientation import department_rows
        boxes = verified_rows.get('boxes', [])
        rows = department_rows(boxes, image)
        if rows and [r['detected_text'] for r in rows] != [
                verified_rows.get('department_text'), verified_rows.get('type_text')]:
            rows = []
    if not rows:
        boxes, rows = _detect_layout_rows(
            source, directory, index, image, rho, reader=reader, provider=provider,
        )
    if evidence is not None:
        # Preserve the independent detector decision before a short-number
        # retry or failed arc construction can replace its local row list.
        evidence['rows'] = rows
    if len(rows) == 1:
        # Whole-stamp detection can miss a short parenthesized number.
        # Reuse the established tight-number locator, not a wide middle band.
        from .number import prepare_number_input
        number = prepare_number_input(
            source, path('number-detection'), rows[0]['box'], reader=reader, provider=provider,
        )
        if number.get('status') != 'detected' or inner_role(number.get('text'), number_field=True) != 'number':
            return None
        l, top, r, bottom = number['box']
        boxes = list(boxes) + [{
            'text': number['text'], 'confidence': number['confidence'],
            'number_field': True,
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
    roles = [row['role'] for row in rows]
    department_layout = roles == ['department', 'type']
    maps = {} if department_layout else _arc_maps(
        masked, rho, guide, allow_empty_lower=True, allow_sparse_lower='phone' in roles,
        allow_continuous_name=roles == ['type', 'number'])
    if maps is None or ('number' in roles and not {'lower', 'company'}.intersection(maps)):
        return None
    continuous_name = 'company' in maps
    overlay = image.copy()
    plan = {
        'method': ('ellipse_department_rows' if department_layout else
                   'ellipse_continuous_name' if continuous_name else 'ellipse_detected_rows'),
        'rows': [], 'arcs': {}, 'jobs': [],
        'expected_inner_roles': roles, 'border': guide.summary(),
        'overlay': path('regions'), 'ring_input': path('ring-input'), 'mask': path('mask'),
    }
    for i, row in enumerate(rows):
        name = f'inner-{i+1}'
        # Keep short department rows in the detector's tight frame. Extra
        # whitespace can change a faint first glyph during recognizer resize.
        save_row_polygon(source, path(name), row['polygon'],
                         padding=0 if row['role'] == 'department' else 20)
        # Keep the full-resolution crop for inspection; read the same pixels
        # at a stable line height rather than letting oversized glyphs vary.
        crop = cv2.imread(str(path(name)))
        read_path = path(name)
        if crop.shape[0] > 64:
            read_path = path(name + '-read')
            size = (max(1, round(crop.shape[1] * 64 / crop.shape[0])), 64)
            if not cv2.imwrite(str(read_path), cv2.resize(crop, size, interpolation=cv2.INTER_AREA)):
                raise ValueError('内部文字识别图保存失败')
        cv2.polylines(overlay, [np.rint(row['polygon']).astype(np.int32)], True, (80, 110, 80), 3)
        plan['rows'].append({**row, 'name': name})
        plan['jobs'].append((name, read_path, 'layout_inner', f'内部文字 {i+1}'))
    cv2.imwrite(str(plan['overlay']), overlay)
    if department_layout:
        from receipt_ocr.imaging.unwrap import save_unwrapped_seal
        # The company can continue around both sides of this stamp. Keep the
        # established full-ring sampler; only its observed interior rows change.
        ring = path('company')
        save_unwrapped_seal(
            plan['ring_input'], ring, None, color=color, elliptical=True,
            normalized=True, single_line=True, direct_ellipse=True, ring_guide=guide,
        )
        plan['jobs'].append(('company', ring, 'ring', '环形公司名称'))
        return plan
    blue, green, red = (c.astype(float) for c in cv2.split(masked))
    # Contact stamps already use the enhanced color image. Amplifying it again
    # clips the thin gaps between dense company-name strokes after unwrapping.
    gain = 1.0 if 'phone' in roles else 255/160
    chroma = red-np.maximum(blue, green) if color == 'red' else blue-np.maximum(red, green)
    gray = (255-np.clip(chroma*gain, 0, 255)).astype('uint8')
    for key, mapping in maps.items():
        pixels = cv2.remap(gray, mapping['x'], mapping['y'], cv2.INTER_CUBIC,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        pixels = cv2.copyMakeBorder(pixels, 12, 12, 20, 20, cv2.BORDER_CONSTANT, value=255)
        if not cv2.imwrite(str(path(key)), pixels):
            raise ValueError('自动分区图片保存失败')
        plan['arcs'][key] = {k: mapping[k] for k in ('degrees', 'radial_range')}
        if mapping.get('border_adjustment'):
            plan['border']['company_arc_correction'] = mapping['border_adjustment']
        plan['jobs'].append((key, path(key), 'layout_lower' if key == 'lower' else 'ring',
                             '环形公司名称' if key == 'company' else
                             '上弧文字' if key == 'upper' else '下弧文字'))
    return plan
