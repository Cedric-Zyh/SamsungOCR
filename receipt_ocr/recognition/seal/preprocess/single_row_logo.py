"""Keep a verified single type row from becoming star/rim component rows."""
import re

import cv2
import numpy as np


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
