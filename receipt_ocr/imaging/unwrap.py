"""Seal ring unwrapping and previews."""
from __future__ import annotations

from receipt_ocr.runtime.image_processing import timed_image

from pathlib import Path
import cv2
import numpy as np
from .contracts import SealRegion
from .io import _read_image
from .working_images import remember_png
from .colors import _ocr_color_mask
from .shapes import seal_region_is_rectangular
from .ellipse import _save_ellipse_annulus_unwrapped, _fit_ellipse_annulus, _ring_text_normal_maps, _ellipse_proportional_maps


def _robust_round_seal_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return a noise-trimmed near-square ink box for a distorted round seal.

    Sparse JPEG chroma speckles can sit far from the actual stamp. Using their
    absolute min/max moves the polar centre and leaves the company arc as a
    wave. This helper stays inactive unless trimming the 0.5% coordinate
    tails changes a clearly non-round raw box into a substantially smaller,
    near-square box while retaining the dense ink core.
    """
    ys, xs = np.where(mask > 0)
    if len(xs) < 200:
        return None
    raw_x1, raw_x2 = int(xs.min()), int(xs.max()) + 1
    raw_y1, raw_y2 = int(ys.min()), int(ys.max()) + 1
    raw_width, raw_height = raw_x2 - raw_x1, raw_y2 - raw_y1
    if raw_width <= 0 or raw_height <= 0:
        return None
    raw_aspect = raw_width / raw_height
    if 0.75 <= raw_aspect <= 1.33:
        return None

    quantile = 0.005
    x1 = int(np.quantile(xs, quantile))
    x2 = int(np.quantile(xs, 1 - quantile)) + 1
    y1 = int(np.quantile(ys, quantile))
    y2 = int(np.quantile(ys, 1 - quantile)) + 1
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0:
        return None
    aspect = width / height
    if not 0.80 <= aspect <= 1.25:
        return None
    if width * height > raw_width * raw_height * 0.82:
        return None
    pad_x = max(2, round(width * 0.035))
    pad_y = max(2, round(height * 0.035))
    return (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(mask.shape[1], x2 + pad_x),
        min(mask.shape[0], y2 + pad_y),
    )


@timed_image('crop', '区域裁剪')
def save_ring_text_band_preview(
    source: str | Path,
    destination: str | Path,
    *,
    color: str = "red",
    ring_guide=None,
    proportional: bool = False,
) -> Path:
    """Show exactly the band sampled by the shared border guide or ellipse."""
    image = _read_image(source)
    mask = _ocr_color_mask(image, color)
    if ring_guide is not None and tuple(mask.shape) != ring_guide.source_shape:
        raise ValueError("印章边框与预览图片坐标不一致")
    geometry = ring_guide.annulus if ring_guide is not None else _fit_ellipse_annulus(mask)
    preview = np.full_like(image, 255)
    if geometry is not None:
        if ring_guide is not None:
            map_x, map_y = ring_guide.map_x, ring_guide.map_y
        elif proportional:
            map_x, map_y = _ellipse_proportional_maps(*geometry)
        else:
            map_x, map_y = _ring_text_normal_maps(mask, geometry)
        band = np.zeros(mask.shape, dtype=np.uint8)
        for row, value in ((0, 255), (-1, 0)):
            polygon = np.rint(np.column_stack((map_x[row], map_y[row]))).astype(np.int32)
            cv2.fillPoly(band, [polygon], value)
        keep = (band > 0) & (mask > 0)
        preview[keep] = image[keep]
    else:
        preview[mask > 0] = image[mask > 0]

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", preview)
    if not ok:
        raise ValueError("环形文字带预览图片编码失败")
    encoded.tofile(str(destination))
    remember_png(destination, preview)
    return destination


@timed_image('unwrap', '环形文字展开')
def save_unwrapped_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion | None,
    *,
    robust_bounds: bool = False,
    exclude_boxes: list[tuple[float, float, float, float]] | None = None,
    elliptical: bool = False,
    color: str = "red",
    normalized: bool = False,
    single_line: bool = False,
    direct_ellipse: bool = False,
    ring_guide=None,
    preserve_full_ring: bool = False,
) -> bool:
    """Unwrap circular stamp text into a horizontal line for conventional OCR.

    ``single_line=True`` produces one revolution for the new ring OCR input.
    The caller may provide an already white-covered center type row; the
    legacy ``exclude_boxes`` path remains available for older evidence.
    ``preserve_full_ring=True`` retains small outer digits at both seam ends
    for independent serial OCR; the company input keeps its default trim.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if region is None:
        # The ellipse route may already have rotated and colour-isolated the
        # QingTong crop.  In that case the whole file is the local stamp and
        # no page-coordinate region should be applied a second time.
        crop = image
    else:
        x1, y1 = int(region.x * width), int(region.y * height)
        x2 = int((region.x + region.width) * width)
        y2 = int((region.y + region.height) * height)
        crop = image[y1:y2, x1:x2]
    # Use the same strict chroma gate as the visible color-isolated artifact.
    # The permissive detector mask is intentionally unsuitable here: faint
    # red JPEG fringes around black table lines become thick horizontal bars
    # after polar unwrapping and can erase the curved company name.
    mask = _ocr_color_mask(crop, region.color if region is not None else color)
    for box in exclude_boxes or ():
        if not box or len(box) != 4:
            continue
        left, top, right, bottom = box
        x1 = max(0, min(mask.shape[1], round(float(left) * mask.shape[1])))
        y1 = max(0, min(mask.shape[0], round(float(top) * mask.shape[0])))
        x2 = max(x1, min(mask.shape[1], round(float(right) * mask.shape[1])))
        y2 = max(y1, min(mask.shape[0], round(float(bottom) * mask.shape[0])))
        mask[y1:y2, x1:x2] = 0
    canvas = np.full(mask.shape, 255, dtype=np.uint8)
    canvas[mask > 0] = 0
    h, w = canvas.shape
    # Rectangular stamps are already linear. Polar unwrapping would bend otherwise
    # readable rows into arcs, so keep a normalized high-resolution view instead.
    # A circular stamp crop often becomes moderately wide (1.45--1.60) when
    # handwriting or a nearby duplicate is joined to its contour.  Treating
    # that crop as a rectangular stamp prevents polar unwrapping of the curved
    # company name.  Reviewed service-center rectangles are materially wider;
    # 1.65 keeps those linear while recovering the ambiguous circular cases.
    if region is not None and seal_region_is_rectangular(source, region):
        scale = 1500 / max(1, w)
        normalized = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", normalized)
        if not ok:
            raise ValueError("矩形印章校正图片编码失败")
        encoded.tofile(str(destination))
        remember_png(destination, normalized)
        return False
    if elliptical and (normalized or direct_ellipse):
        # Keep anti-aliased red strokes for the v6 recognizer; hard threshold
        # turns the thin oval company glyphs into broken blobs.
        b, g, r = (channel.astype(np.float32) for channel in cv2.split(crop))
        if (region.color if region is not None else color) == "red":
            dominance = r - np.maximum(g, b)
        else:
            dominance = b - np.maximum(g, r)
        gray = 255.0 - np.clip(dominance * (255.0 / 160.0), 0.0, 255.0)
        if _save_ellipse_annulus_unwrapped(
            gray.astype(np.uint8), mask, destination, single_line=single_line,
            ring_guide=ring_guide, trim_whitespace=not preserve_full_ring,
            proportional=direct_ellipse,
        ):
            return False
        if direct_ellipse:
            raise ValueError("椭圆边框不足以可靠展开，请核对原图")
    # Normalize the colored-ink bounding box to a square before polar
    # unwrapping.  Padding a 2:1 oval into a square leaves its text on an
    # ellipse, which a circular polar transform bends into waves.  Stretching
    # only this OCR derivative preserves the user-visible original crop while
    # making oval company text horizontal enough for conventional OCR.
    points = cv2.findNonZero(mask)
    used_robust_bounds = False
    if points is not None and not normalized:
        robust = _robust_round_seal_bounds(mask) if robust_bounds else None
        if robust is not None:
            bx1, by1, bx2, by2 = robust
            used_robust_bounds = True
        else:
            bx, by, bw, bh = cv2.boundingRect(points)
            pad_x = max(2, round(bw * 0.035))
            pad_y = max(2, round(bh * 0.035))
            bx1, by1 = max(0, bx - pad_x), max(0, by - pad_y)
            bx2, by2 = min(w, bx + bw + pad_x), min(h, by + bh + pad_y)
        canvas = canvas[by1:by2, bx1:bx2]
    size = max(canvas.shape[:2])
    square = (
        canvas
        if normalized and canvas.shape[0] == canvas.shape[1]
        else cv2.resize(canvas, (size, size), interpolation=cv2.INTER_CUBIC)
    )
    radius = size / 2
    polar = cv2.warpPolar(
        square,
        (int(radius), 1440),
        (size / 2, size / 2),
        radius,
        cv2.WARP_POLAR_LINEAR | cv2.WARP_FILL_OUTLIERS,
    )
    if single_line:
        # OpenCV's polar angle starts on the right-hand ray.  Move the
        # bottom ray to the seam before rotating the polar image into a
        # horizontal OCR line.  This keeps a correctly oriented round stamp's
        # lower edge as the default reading start instead of cutting through
        # an arbitrary character on the right-hand ray.
        polar = np.roll(polar, -(polar.shape[0] // 4), axis=0)
    strips = []
    # One revolution. No repeated company text from the three historical
    # phase-shifted rows.
    for shift in ((0,) if single_line else (0, 480, 960)):
        shifted = np.roll(polar, shift, axis=0)
        # Drop the outermost border ring.  After unwrapping it becomes a
        # full-width black bar above/below every text strip and can dominate
        # OCR detection.  Company glyphs sit just inside this border, so keep
        # the 42%–94% radial band rather than the complete outer radius.
        inner_radius, outer_radius = (
            (0.54, 0.90) if elliptical else (0.42, 0.94)
        )
        outer = shifted[:, int(radius * inner_radius) : int(radius * outer_radius)]
        strip = cv2.rotate(outer, cv2.ROTATE_90_COUNTERCLOCKWISE)
        strips.append(cv2.resize(strip, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC))
    gap = np.full((18, max(strip.shape[1] for strip in strips)), 255, dtype=np.uint8)
    normalized = [
        cv2.copyMakeBorder(strip, 0, 0, 0, gap.shape[1] - strip.shape[1], cv2.BORDER_CONSTANT, value=255)
        for strip in strips
    ]
    strip = normalized[0]
    for extra in normalized[1:]:
        strip = np.vstack((strip, gap, extra))
    # The circular/oval outline becomes one or two very long horizontal arcs
    # after polar unwrapping. Remove only strokes spanning at least one fifth
    # of the output width; Chinese glyph components are far shorter, so their
    # evidence remains intact while the intermediate image becomes auditable
    # and substantially easier for text detection.
    unwrapped_ink = cv2.bitwise_not(strip)
    border_lines = cv2.morphologyEx(
        unwrapped_ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(80, strip.shape[1] // 5), 2)
        ),
    )
    border_lines = cv2.dilate(
        border_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5))
    )
    strip = cv2.bitwise_not(cv2.subtract(unwrapped_ink, border_lines))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", strip)
    if not ok:
        raise ValueError("圆形印章展开图片编码失败")
    encoded.tofile(str(destination))
    remember_png(destination, strip)
    return used_robust_bounds
