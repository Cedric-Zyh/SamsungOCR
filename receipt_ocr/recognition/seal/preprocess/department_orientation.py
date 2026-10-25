"""Verify a department-based rotation with two complete internal text rows."""
from pathlib import Path
import math

import cv2
import numpy as np

from ..layout_fields import inner_role
from ..ocr.interface import OcrReader
from .detected_layout import detected_rows, fitted_guide
from .horizontal_rows import polygon_bounds
from .orientation_geometry import _rotation_geometry, _transform_points, rotate_stamp_image
from .row_extent import save_row_polygon


def department_rows(boxes, image):
    """Require two separate, nearly level rows inside the measured stamp rim."""
    color = 'blue' if image[:, :, 0].mean() > image[:, :, 2].mean() else 'red'
    guide = fitted_guide(image, color=color)
    if guide is None:
        return []
    (cx, cy), (a, b), angle = guide.annulus[0][2]
    yy, xx = np.indices(image.shape[:2])
    t = np.deg2rad(angle)
    dx, dy = xx-cx, yy-cy
    rho = np.hypot((dx*np.cos(t)+dy*np.sin(t))/(a/2),
                   (-dx*np.sin(t)+dy*np.cos(t))/(b/2))
    rows = detected_rows(boxes, image, rho)
    if [row['role'] for row in rows] != ['department', 'type']:
        return []
    angles = []
    for row in rows:
        points = np.asarray(row['polygon'])
        dx, dy = points[1]-points[0]
        angles.append(math.degrees(math.atan2(dy, dx)))
        if row['detected_confidence'] < .90:
            return []
    if max(abs(a) for a in angles) > 3 or abs(angles[0]-angles[1]) > 3:
        return []
    return rows


def prepare_department_candidate(source, scratch, band_dir, item, *, model_variant):
    """A department only proposes an angle; fresh purpose OCR must verify it."""
    from .orientation_angles import _stamp_type_marker_strength

    coarse = float(item['angle'])
    width, height = item['candidate_width'], item['candidate_height']
    anchors = []
    for box in item.get('boxes', []):
        if inner_role(box.get('text', '')) != 'department':
            continue
        points = np.asarray(box.get('points', []), dtype=float)
        confidence = float(box.get('confidence') or 0)
        if points.shape != (4, 2) or not np.isfinite(points).all() or not confidence >= .90:
            continue
        cx, cy = points.mean(axis=0)
        dx, dy = points[1]-points[0]
        angle = math.degrees(math.atan2(dy, dx))
        if (not .2*width < cx < .8*width or not .2*height < cy < .7*height
                or abs(angle) > 45 or np.hypot(dx, dy) < 1.5*np.linalg.norm(points[3]-points[0])):
            continue
        anchors.append((confidence, box, angle))
    if len(anchors) != 1:
        return None
    confidence, anchor, fine = anchors[0]
    fine = 0.0 if abs(fine) < 2 else fine
    coarse_source = Path(source) if coarse == 0 else Path(scratch)/f'coarse-{int(coarse)}.png'
    trial_dir = Path(scratch)/f'department-{coarse:g}'
    trial_dir.mkdir(parents=True, exist_ok=True)
    final = trial_dir/'oriented.png'
    rotate_stamp_image(coarse_source, final, fine)
    image = cv2.imread(str(final))
    fh, fw = image.shape[:2]
    matrix, _, _ = _rotation_geometry(width, height, fine)
    # This intermediate scale retained both full rows where 512 and native
    # detection split the faint purpose into individual characters.
    scale = min(1., 768/max(fw, fh))
    preview = trial_dir/'detect.png'
    pw, ph = round(fw*scale), round(fh*scale)
    cv2.imwrite(str(preview), cv2.resize(image, (pw, ph), interpolation=cv2.INTER_AREA))
    reader = OcrReader()
    boxes = []
    for box in reader.detect_boxes(preview, model_variant=model_variant):
        points = np.asarray(box.get('points', []), dtype=float)
        if points.shape == (4, 2) and np.isfinite(points).all():
            boxes.append({**box, 'points': (points*[fw/pw, fh/ph]).tolist()})
    rows = department_rows(boxes, image)
    if not rows or rows[0]['detected_text'] != anchor['text']:
        return None
    if _stamp_type_marker_strength(rows[1]['detected_text']) < 2:
        return None
    evidence = Path(band_dir)/f'department-{coarse:g}'
    evidence.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows):
        crop = evidence/f'row-{i}.png'
        save_row_polygon(final, crop, row['polygon'], padding=0 if i == 0 else 20)
        pixels = cv2.imread(str(crop))
        read_path = crop
        if pixels.shape[0] > 64:
            read_path = evidence/f'row-{i}-read.png'
            cv2.imwrite(str(read_path), cv2.resize(pixels,
                (max(1, round(pixels.shape[1]*64/pixels.shape[0])), 64), interpolation=cv2.INTER_AREA))
        reads = reader.read_line(read_path, model_variant=model_variant)
        if (len(reads) != 1 or reads[0].text != row['detected_text']
                or not float(reads[0].confidence or 0) >= .90):
            return None
        row['verified_confidence'] = float(reads[0].confidence)
    purpose = rows[1]
    inverse = cv2.invertAffineTransform(matrix)
    original_boxes = [{**box, 'points': _transform_points(box['points'], inverse)} for box in boxes]
    purpose_points = _transform_points(purpose['polygon'], inverse)
    exact = polygon_bounds([purpose['polygon']], fw, fh)
    return dict(
        angle=coarse, path=final, fine_rotation=fine, fine_matrix=matrix,
        candidate_width=width, candidate_height=height, final_width=fw, final_height=fh,
        focus_box=exact, exact_box=exact, mask_polygons=[purpose['polygon']],
        anchor_text=anchor['text'], anchor_confidence=confidence, anchor_angle=fine,
        anchor_points=anchor['points'], boxes=original_boxes,
        decision={'anchor_text': purpose['detected_text'], 'anchor_points': purpose_points, 'angle': fine},
        band_text=purpose['detected_text'], band_confidence=purpose['verified_confidence'], error='',
        department_verified_rows={'coordinate_frame': 'final_oriented_stamp',
            'image_size': [fw, fh], 'boxes': boxes,
            'department_text': rows[0]['detected_text'], 'type_text': purpose['detected_text']},
    )
