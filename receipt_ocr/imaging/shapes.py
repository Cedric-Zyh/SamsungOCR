"""Seal shape classification."""
from __future__ import annotations

from pathlib import Path
import cv2
from .contracts import SealRegion
from .io import _read_image
from .colors import _ocr_color_mask


def seal_region_is_rectangular(
    source: str | Path,
    region: SealRegion,
) -> bool:
    """Distinguish a linear rectangle from a wide oval stamp.

    Aspect ratio alone misclassifies common 2:1 oval customer stamps.  A
    rectangular border occupies almost all of its rotated bounding box,
    whereas an oval occupies roughly pi/4.  Use the colored border geometry
    and retain the historical aspect-ratio fallback only when no usable
    contour survives the chroma gate.  The convex hull is measured instead
    of the raw contour: lettering and broken ink make a real rectangular
    border highly concave, even though its four outer corners remain clear.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    ratio = max(crop.shape[:2]) / max(1, min(crop.shape[:2]))
    if ratio < 1.30:
        return False
    if crop.size == 0:
        return ratio >= 1.45
    mask = _ocr_color_mask(crop, region.color)
    kernel_size = max(3, round(min(mask.shape[:2]) * 0.018))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    grouped = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(
        grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        # QingTong is useful as a last-resort hint when the local colour mask
        # is too faint to recover a contour.  It must not override a usable
        # local geometry result: the API occasionally calls an oval a
        # rectangle (or a circle).
        return str(region.qingtong_cls).strip().lower() in {
            "rectangle", "rect", "矩形"
        } or ratio >= 1.45
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < mask.size * 0.025:
        return str(region.qingtong_cls).strip().lower() in {
            "rectangle", "rect", "矩形"
        } or ratio >= 1.45
    rect = cv2.minAreaRect(contour)
    rw, rh = rect[1]
    # Text inside a rectangular stamp creates deep concavities in the
    # connected contour.  Its convex hull preserves the four outer corners;
    # an oval's hull still fills only about pi/4 of its rotated box.
    hull = cv2.convexHull(contour)
    extent = cv2.contourArea(hull) / max(1.0, rw * rh)
    # Evaluate corners on the same outer hull as the extent.  Lettering and
    # gaps in the border add concave vertices to the raw contour, even when
    # the stamp's outer boundary is an unambiguous rectangle.
    perimeter = cv2.arcLength(hull, True)
    vertices = len(cv2.approxPolyDP(hull, perimeter * 0.025, True))
    return bool(extent >= 0.84 and vertices <= 8)


def seal_region_is_elliptical(
    source: str | Path,
    region: SealRegion,
) -> bool:
    """Classify a non-rectangular stamp as an oval from its own pixels.

    QingTong's ``cls`` is retained as a fallback for an extremely faint crop,
    but a usable local colour mask wins.  The decision is intentionally based
    on the ink bounding box rather than the API label: a horizontal oval has a
    stable long/short-axis ratio even when its inner text is fragmented.
    """
    if seal_region_is_rectangular(source, region):
        return False
    try:
        image = _read_image(source)
        height, width = image.shape[:2]
        x1, y1 = int(region.x * width), int(region.y * height)
        x2 = int((region.x + region.width) * width)
        y2 = int((region.y + region.height) * height)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            raise ValueError("椭圆章区域为空")
        mask = _ocr_color_mask(crop, region.color)
        points = cv2.findNonZero(mask)
        if points is not None:
            bx, by, bw, bh = cv2.boundingRect(points)
            axis_ratio = max(bw, bh) / max(1.0, min(bw, bh))
            if axis_ratio >= 1.18:
                return True
            # A contour fit is more stable than the crop box when the API
            # added generous margins around a nearly circular stamp.
            if len(points) >= 5:
                ellipse = cv2.fitEllipse(points)
                major, minor = sorted(ellipse[1], reverse=True)
                if major / max(1.0, minor) >= 1.18:
                    return True
        label = str(region.qingtong_cls).strip().lower()
        return label in {"ellipse", "oval", "椭圆"}
    except (OSError, ValueError, TypeError, cv2.error):
        label = str(region.qingtong_cls).strip().lower()
        return label in {"ellipse", "oval", "椭圆"}


def seal_region_shape(source: str | Path, region: SealRegion) -> str:
    """Return the locally validated shape: ``rectangle``, ``ellipse`` or ``circle``."""
    if seal_region_is_rectangular(source, region):
        return "rectangle"
    if seal_region_is_elliptical(source, region):
        return "ellipse"
    return "circle"
