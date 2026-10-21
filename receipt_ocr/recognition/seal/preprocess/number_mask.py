"""Remove a corroborated interior number from the ring OCR input only."""
import math

import cv2
import numpy as np

from .number import MIN_NUMBER_CONFIDENCE, seal_number


def _complete_number_box(image, box):
    """Recover glyph tips cut by a tight OCR crop, without absorbing the rim."""
    left, top, right, bottom = box
    height, width = bottom-top, right-left
    margin = max(4, round(height*.15))
    ink = (image.max(axis=2).astype(np.int16)-image.min(axis=2) >= 16).astype('uint8')
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    overlaps = np.bincount(labels[top:bottom, left:right].ravel(), minlength=len(stats))
    bounds = [left, top, right, bottom]
    for i, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if (area < 3 or overlaps[i] < .60*area or h > height*1.3 or w > width
                or x < left-margin or y < top-margin
                or x+w > right+margin or y+h > bottom+margin):
            continue
        # Include the same six-pixel interpolation halo used by the shared
        # ink mask; the whole bounding rectangle is never painted white.
        bounds = [min(bounds[0], max(0, int(x)-6)),
                  min(bounds[1], max(0, int(y)-6)),
                  max(bounds[2], min(image.shape[1], int(x+w)+6)),
                  max(bounds[3], min(image.shape[0], int(y+h)+6))]
    return bounds


def white_verified_number(ring_input, number_input, evidence, mask_path, combined_mask,
                          *, reader, provider):
    box = evidence.get('box')
    expected = seal_number(evidence.get('text'))
    confidence = float(evidence.get('confidence') or 0)
    if (evidence.get('status') != 'detected' or not expected
            or not math.isfinite(confidence) or confidence < MIN_NUMBER_CONFIDENCE
            or not isinstance(box, (tuple, list)) or len(box) != 4):
        return {'status': 'unverified'}
    image = cv2.imread(str(ring_input))
    coords = np.asarray(box, float)
    if image is None or not np.isfinite(coords).all():
        return {'status': 'invalid_box'}
    left, top, right, bottom = np.rint(coords).astype(int).tolist()
    if not (0 <= left < right <= image.shape[1] and 0 <= top < bottom <= image.shape[0]):
        return {'status': 'invalid_box'}
    reads = reader.read_line(number_input, provider=provider)
    if len(reads) != 1:
        return {'status': 'unverified'}
    read = reads[0]
    score = float(read.confidence or 0)
    if (seal_number(read.text) != expected or not math.isfinite(score)
            or score < MIN_NUMBER_CONFIDENCE):
        return {'status': 'unverified', 'text': read.text, 'confidence': score}
    # Reuse containment-based masking: a frame or a neighbouring stroke
    # crossing the number box stays untouched. Never whiten the search band.
    from .inputs import _white_detected_type_region
    completed_box = _complete_number_box(image, (left, top, right, bottom))
    _white_detected_type_region(ring_input, ring_input, completed_box,
                                mask_destination=mask_path)
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    previous = cv2.imread(str(combined_mask), cv2.IMREAD_GRAYSCALE)
    if previous is not None and previous.shape == mask.shape:
        if not cv2.imwrite(str(combined_mask), cv2.bitwise_or(previous, mask)):
            raise ValueError('横排与编号填白范围保存失败')
    count = int(np.count_nonzero(mask))
    return {'status': 'applied' if count else 'no_contained_ink',
            'text': read.text, 'confidence': score, 'box': completed_box,
            'detected_box': [left, top, right, bottom],
            'method': 'verified_number_contained_ink', 'pixel_count': count}
