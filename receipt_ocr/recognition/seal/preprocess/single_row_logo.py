"""Keep a verified single type row from becoming star/rim component rows."""
import re
from pathlib import Path

import cv2
import numpy as np

from ..layout_fields import inner_role
from .row_extent import save_row_polygon


def verified_single_type_evidence(source, directory, index, image, orientation, *, reader, provider):
    """Recheck a complete type row before adopting unverified component rows.

    Only the component fallback calls this. Verified number, phone, station
    and department layouts have already taken priority. The current requested
    stamp wording is never used to supply text or select this route.
    """
    orientation = orientation or {}
    text = str(orientation.get('type_band_text') or '')
    confidence = float(orientation.get('type_band_confidence') or 0)
    polygons = orientation.get('oriented_type_row_polygons') or []
    raw_box = orientation.get('oriented_type_row_exact_box')
    if (not np.isfinite(confidence) or confidence < .95 or inner_role(text) != 'type'
            or not polygons or raw_box is None):
        return None
    box, polygon = np.asarray(raw_box, float), np.asarray(polygons[0], float)
    height, width = image.shape[:2]
    if (box.shape != (4,) or polygon.shape != (4, 2)
            or not np.isfinite(box).all() or not np.isfinite(polygon).all()
            or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
        return None
    path = Path(directory) / f'seal-{index}-verified-single-type.png'
    try:
        save_row_polygon(source, path, polygon, padding=10)
        observations = reader.read_line(path, provider=provider)
        if (len(observations) != 1 or observations[0].text != text
                or not np.isfinite(observations[0].confidence)
                or observations[0].confidence < .95):
            return None
        boxes = reader.detect_boxes(source, provider=provider)
    except Exception:
        # An unavailable check cannot erase an otherwise usable multi-row plan.
        return None
    for row in boxes:
        points = np.asarray(row.get('points', []), float)
        score = float(row.get('confidence') or 0)
        if points.shape != (4, 2) or not np.isfinite(points).all() or not np.isfinite(score):
            return None
        if score < .80:
            continue
        middle = points.mean(axis=0)
        if not (.23 * width < middle[0] < .77 * width
                and .27 * height < middle[1] < .90 * height):
            continue
        if row.get('text') == text:
            continue
        row_height = float(np.linalg.norm(points[3] - points[0]))
        left, top, right, bottom = box
        covered = np.mean(
            (points[:, 0] >= left - .1 * row_height)
            & (points[:, 0] <= right + .1 * row_height)
            & (points[:, 1] >= top - .1 * row_height)
            & (points[:, 1] <= bottom + .1 * row_height)) >= .75
        if not covered:
            # Unknown but reliable interior words also veto the single-row
            # decision; do not restrict protection to known phone/number roles.
            return None
    return {
        'method': 'independently_verified_single_type', 'selected': 'single_type_row',
        'type_text': text, 'type_box': box.tolist(),
        'type_recheck_confidence': float(observations[0].confidence),
        'type_recheck_image': path.name, 'extra_interior_text': [],
    }


def _central_star(image, type_height):
    height, width = image.shape[:2]
    chroma = image.max(2).astype(float) - image.min(2)
    ink = cv2.morphologyEx((chroma > 35).astype('uint8'), cv2.MORPH_CLOSE,
                          np.ones((3, 3), np.uint8))
    # RETR_LIST also finds the disconnected star enclosed by the outer rim.
    contours, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if (not .12 * height < h < .35 * height or not .65 < w / h < 1.55
                or not .6 * type_height < h < 2.2 * type_height
                or abs(x+w/2-width/2) > .15*width
                or abs(y+h/2-height/2) > .20*height):
            continue
        perimeter = cv2.arcLength(contour, True)
        polygons = [cv2.approxPolyDP(contour, scale*perimeter, True)
                    for scale in (.02, .03)]
        if any(len(polygon) != 10 for polygon in polygons):
            continue
        polygon = polygons[0]
        hull = cv2.convexHull(polygon, returnPoints=False)
        defects = cv2.convexityDefects(polygon, hull)
        if defects is None or sum(d[0][3]/256 > .10*min(w, h) for d in defects) != 5:
            continue
        candidates.append([x, y, x+w, y+h])
    return candidates[0] if len(candidates) == 1 else None


def single_row_logo_evidence(image, rho, component_rows, detected, orientation):
    """Require an independently read type, a five-point star and no extra row.

    A failed multi-row sampler is not permission to turn a rim glyph's inner
    strokes or a logo into text. This only selects the existing ordinary stamp
    path; its complete type/number localization and ring OCR still run there.
    Required business wording is never supplied to this decision.
    """
    rows = detected.get('rows', [])
    if len(rows) != 1 or rows[0].get('role') != 'type' or not orientation:
        return None
    observed = rows[0]
    text = re.sub(r'\s+', '', observed['detected_text'])
    verified = re.sub(r'\s+', '', str(orientation.get('type_band_text') or ''))
    scores = [observed.get('detected_confidence', 0), orientation.get('type_band_confidence', 0)]
    if (text != verified or len(text) < 3
            or not np.isfinite(scores).all() or min(scores) < .95):
        return None
    box = np.asarray(observed['box'], float)
    type_height = box[3]-box[1]
    if not np.isfinite(box).all() or type_height <= 0 or box[2]-box[0] < 3*type_height:
        return None
    star = _central_star(image, type_height)
    if star is None or star[3] > box[1]:
        return None
    kinds = []
    for row in component_rows:
        left, top, right, bottom = row['box']
        part = np.array([left, top, right, bottom])
        if np.all(part[:2] >= box[:2]-.15*type_height) and np.all(part[2:] <= box[2:]+.15*type_height):
            kinds.append('type')
        elif (left <= star[0] and top <= star[1] and right >= star[2] and bottom >= star[3]
              and bottom-top < 1.8*type_height):
            kinds.append('star_artwork')
        elif (0 <= left < right <= image.shape[1] and 0 <= top < bottom <= image.shape[0]
              and right-left < .9*type_height and bottom-top < .75*type_height
              and bottom < star[1] and rho[top:bottom, left:right].min() > .48):
            kinds.append('rim_fragment')
        else:
            return None  # Another independent row must remain on the multi-row path.
    if kinds.count('type') != 1 or kinds.count('star_artwork') != 1:
        return None
    return {'method': 'verified_single_type_and_star', 'selected': 'single_type_row',
            'type_text': text, 'type_box': box.tolist(), 'star_box': star,
            'rejected_component_roles': [kind for kind in kinds if kind != 'type']}
