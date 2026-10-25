"""Find a short parenthesized number overlapping a padded purpose-row box."""
import math
from pathlib import Path
import re
import tempfile

import cv2
import numpy as np

from .number_mask import _complete_number_box
from receipt_ocr.domain.parsing.seal_numbers import normalize_stamp_number_letters, stamp_number_token


def prepare_overlapping_number(source, destination, focus_box, *, reader, provider):
    image = cv2.imread(str(source))
    if image is None:
        return None
    left, top, right, bottom = map(float, focus_box)
    height, width = bottom-top, right-left
    if not np.isfinite(focus_box).all() or height < 8 or width < height*2:
        return None
    cx = (left+right)/2
    search_box = [max(0, round(cx-width*.22)), max(0, round(bottom-height*.30)),
                  min(image.shape[1], round(cx+width*.22)),
                  min(image.shape[0], round(bottom+height*.45))]
    l, t, r, b = search_box
    if l >= r or t >= b:
        return None
    with tempfile.TemporaryDirectory(prefix='receipt-number-overlap-') as scratch:
        search = Path(scratch)/'search.png'
        cv2.imwrite(str(search), image[t:b, l:r])
        boxes = reader.detect_boxes(search, provider=provider)
    candidates = []
    for item in boxes or []:
        text = re.sub(r'\s+', '', str(item.get('text') or ''))
        confidence = float(item.get('confidence') or 0)
        points = np.asarray(item.get('points', []), float)
        if (not re.fullmatch(r'[（(][0-9０-９]{1,3}[）)]', normalize_stamp_number_letters(stamp_number_token(text), isolated=True))
                or not math.isfinite(confidence) or confidence < .90
                or points.shape != (4, 2) or not np.isfinite(points).all()):
            continue
        x0, y0 = points.min(0)
        x1, y1 = points.max(0)
        edge = points[1]-points[0]
        if (x0 <= 0 or y0 <= 0 or x1 >= r-l-1 or y1 >= b-t-1
                or abs(np.rad2deg(np.arctan2(edge[1], edge[0]))) > 15
                or not .20*height <= y1-y0 <= .8*height
                or not 0 < x1-x0 <= .35*width):
            continue
        # Only recover numbers actually overlapping the original lower edge.
        # Rows wholly below it have already been handled by the normal search.
        if not t+y0 < bottom < t+y1:
            continue
        raw_box = np.rint([l+x0, t+y0, l+x1, t+y1]).astype(int).tolist()
        box = _complete_number_box(image, raw_box)
        candidates.append({'text': text, 'confidence': confidence, 'box': box})
    if len(candidates) != 1:
        return None
    selected = candidates[0]
    l, t, r, b = selected['box']
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image[t:b, l:r]):
        raise ValueError('编号区域保存失败')
    return {**selected, 'status': 'detected', 'method': 'overlapping_type_number_search',
            'search_box': search_box, 'candidates': candidates}
