"""Conservative source-page evidence for excluding shipping stamps."""
from __future__ import annotations

import math

import cv2
import numpy as np

from .colors import _ocr_color_mask
from .ellipse import _fit_ellipse_annulus
from .io import _read_image

_CUSTOMER_ROLES = {'收货客户章', '客户章', 'customer'}
_EPSILON = 1e-9


def _geometry(region):
    try:
        x, y, width, height = (float(region[key]) for key in ('x', 'y', 'width', 'height'))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (not all(math.isfinite(value) for value in (x, y, width, height))
            or min(x, y) < 0 or min(width, height) <= 0
            or x + width > 1.001 or y + height > 1.001):
        return None
    return x, y, width, height


def _horizontal_oval(image, region, box):
    """Require a large observed rim; unknown shapes must keep their request."""
    height, width = image.shape[:2]
    x, y, w, h = box
    crop = image[int(y * height):min(height, math.ceil((y + h) * height)),
                 int(x * width):min(width, math.ceil((x + w) * width))]
    color = region.get('color')
    if crop.size == 0 or min(crop.shape[:2]) < 12 or color not in {'red', 'blue'}:
        return False
    try:
        annulus = _fit_ellipse_annulus(_ocr_color_mask(crop, color))
        if annulus is None or annulus[0][1] > .04:
            return False
        (cx, cy), (a, b), angle = annulus[0][2]
        if not np.isfinite([cx, cy, a, b, angle]).all() or min(a, b) <= 0:
            return False
        major_angle = angle + (90 if b > a else 0)
        if (not 1.4 <= max(a, b) / min(a, b) <= 3.5
                or abs(math.sin(math.radians(major_angle))) > math.sin(math.radians(25))):
            return False
        theta = math.radians(angle)
        span_x = math.hypot(a * math.cos(theta), b * math.sin(theta))
        span_y = math.hypot(a * math.sin(theta), b * math.cos(theta))
        ch, cw = crop.shape[:2]
        return bool(span_x >= cw * .65 and span_y >= ch * .55
                    and abs(cx - cw / 2) <= cw * .2 and abs(cy - ch / 2) <= ch * .2)
    except (cv2.error, ValueError, TypeError, ZeroDivisionError):
        return False


def sender_region_indices(source, regions):
    """Return list positions, accepting detected regions and persisted dicts.

    Explicit sender roles are sufficient. For a low-left region that the
    generic detector labels as a customer, require both a separate right
    customer on the same footer and a reliable horizontal oval rim. Position
    alone cannot exclude a displaced customer stamp or an uncertain crop.
    This measures geometry only, without OCR, orientation or image changes.
    """
    regions = [item.to_dict() if hasattr(item, 'to_dict') else
               item if isinstance(item, dict) else {} for item in regions]
    senders = {index for index, item in enumerate(regions) if item.get('role') == '发货单位章'}
    boxes = [_geometry(item) for item in regions]
    paired = []
    for index, box in enumerate(boxes):
        if index in senders or box is None or regions[index].get('role') not in _CUSTOMER_ROLES:
            continue
        x, y, w, h = box
        if x + w / 2 > .42 + _EPSILON or y + h / 2 < .62 - _EPSILON:
            continue
        for other, candidate in enumerate(boxes):
            if (other == index or other in senders or candidate is None
                    or regions[other].get('role') not in _CUSTOMER_ROLES):
                continue
            rx, ry, rw, rh = candidate
            vertical_overlap = min(y + h, ry + rh) - max(y, ry)
            if (rx + rw / 2 >= .55 - _EPSILON and rx >= x + w + .03 - _EPSILON
                    and abs(ry + rh / 2 - (y + h / 2)) <= .15 + _EPSILON
                    and vertical_overlap >= min(h, rh) * .25 - _EPSILON):
                paired.append(index)
                break
    if not paired or source is None:
        return senders
    try:
        image = _read_image(source)
    except (OSError, ValueError):
        return senders
    senders.update(index for index in paired if _horizontal_oval(image, regions[index], boxes[index]))
    return senders
