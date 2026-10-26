"""Seal shape classification."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from .contracts import SealRegion
from .io import _read_image
from .colors import _ocr_color_mask
from .ellipse import _fit_ellipse_annulus


def _has_rectangular_frame(contour, rect) -> bool:
    """Require four supported corners and sustained sides in the box frame.

    A noisy oval can fill more than 84% of its box and simplify to eight
    vertices. Its boundary still misses the corners. Normalize coordinates
    to make the same checks work for tilted, tall, and small rectangles.
    """
    corners = cv2.boxPoints(rect).astype(np.float64)
    u, v = corners[1] - corners[0], corners[3] - corners[0]
    if min(np.dot(u, u), np.dot(v, v)) < 1:
        return False
    points = contour.reshape(-1, 2).astype(np.float64) - corners[0]
    points = np.column_stack((points @ u / np.dot(u, u), points @ v / np.dot(v, v)))
    targets = np.array([[0, 0], [1, 0], [1, 1], [0, 1]])
    distances = np.linalg.norm(points[:, None] - targets, axis=2).min(axis=0)
    if np.any(distances > .12):
        return False
    # Sample actual contour edges uniformly: a straight side may have just two
    # vertices. Do not count convex-hull bridges across missing edges as ink.
    samples = np.concatenate([
        a + (b - a) * np.linspace(0, 1, max(2, int(np.ceil(np.max(np.abs(b-a))*256))))[:, None]
        for a, b in zip(points, np.roll(points, -1, axis=0))
    ])
    supported_sides = 0
    for axis, edge in ((0, 0), (0, 1), (1, 0), (1, 1)):
        along = samples[np.abs(samples[:, axis] - edge) <= .06, 1-axis]
        coverage = len(np.unique(np.clip((along * 128).astype(int), 0, 127))) / 128
        supported_sides += coverage >= .55
    if supported_sides == 4:
        return True
    # A crop/table line may remove one side of an otherwise clear rectangle.
    # All four corners and three real sides still have to be supported; only
    # near-complete box geometry may use this bounded occlusion allowance.
    extent = cv2.contourArea(cv2.convexHull(contour)) / max(1, np.prod(rect[1]))
    return bool(supported_sides == 3 and extent >= .90)


def adaptive_rectangular_frame(image, color='red'):
    """Find the actual outer rectangle through a coloured paper wash.

    A second chroma population is measurement evidence only. Keep all source
    pixels for OCR, and require a large, corner-supported frame at every
    threshold so a star or a text box inside a faint oval cannot replace it.
    """
    pixels = image.astype(np.int16)
    dominant = pixels[:, :, 2] if color == 'red' else pixels[:, :, 0]
    other = np.maximum(pixels[:, :, 0], pixels[:, :, 1]) if color == 'red' else np.maximum(pixels[:, :, 1], pixels[:, :, 2])
    chroma = np.clip(dominant - other, 0, 255).astype(np.uint8)
    threshold, _ = cv2.threshold(chroma, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if threshold <= 25:
        return None
    kernel_size = max(3, round(min(chroma.shape) * .018))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    for cutoff in (int(threshold), 25):
        mask = (chroma > cutoff).astype(np.uint8)
        grouped = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        rect = cv2.minAreaRect(contour)
        area = float(np.prod(rect[1]))
        hull = cv2.convexHull(contour)
        vertices = len(cv2.approxPolyDP(hull, cv2.arcLength(hull, True) * .025, True))
        if (area >= chroma.size * .35 and cv2.contourArea(hull) / max(1, area) >= .84
                and vertices <= 8 and _has_rectangular_frame(contour, rect)):
            return rect, cutoff
    return None


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
    if crop.size == 0:
        return ratio >= 1.45
    # Near-square and tall forms can still have four straight frame sides.
    # Test their border geometry just like wide rectangles; aspect ratio is
    # only a fallback when no usable colored contour survives.
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
    return bool((extent >= 0.84 and vertices <= 8 and _has_rectangular_frame(contour, rect))
                or adaptive_rectangular_frame(crop, region.color) is not None)


def seal_region_is_elliptical(
    source: str | Path,
    region: SealRegion,
) -> bool:
    """Classify a non-rectangular stamp as an oval from its own pixels.

    QingTong's ``cls`` is retained as a fallback for an extremely faint crop,
    but a usable local border wins, including a locally measured circle.
    Stray table-line fragments must not stretch the ink bounding box into an
    oval, and a remote hint must not override circular local geometry.
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
        annulus = _fit_ellipse_annulus(mask)
        if annulus is not None and annulus[0][1] <= .06:
            axes = annulus[0][2][1]
            if min(axes) >= min(crop.shape[:2]) * .35:
                return max(axes) / max(1.0, min(axes)) >= 1.18
        points = cv2.findNonZero(mask)
        if points is not None and len(points) >= max(5, mask.size * .01):
            # If the border is broken, a broad, finite colour fit can still
            # settle the shape. Never let a noisy bounding-box tail override
            # a near-circular fit of the actual stamp ink.
            ellipse = cv2.fitEllipse(points)
            major, minor = sorted(ellipse[1], reverse=True)
            if (np.isfinite([major, minor]).all()
                    and minor >= min(crop.shape[:2]) * .35):
                return major / max(1.0, minor) >= 1.18
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
