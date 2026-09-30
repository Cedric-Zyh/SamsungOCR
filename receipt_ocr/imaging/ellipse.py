"""Ellipse normalization and annulus sampling."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from .contracts import SealRegion
from .io import _read_image
from .colors import _ocr_color_mask


def save_ellipse_normalized_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion | None = None,
    *,
    color: str = "red",
) -> Path:
    """Stretch a horizontal/vertical oval into a square, color-safe image.

    This is a real pipeline stage, not an internal resize hidden inside the
    polar unwrap.  The colored-ink bounding box is cropped with a small
    margin, its shorter axis is stretched to the longer axis, and the result
    is written as a visible white-background artifact.  Later oval OCR stages
    must consume this image so the oval's ring text is treated like a circle.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if region is not None:
        x1 = max(0, int(region.x * width))
        y1 = max(0, int(region.y * height))
        x2 = min(width, int((region.x + region.width) * width))
        y2 = min(height, int((region.y + region.height) * height))
        crop = image[y1:y2, x1:x2]
        ink_color = region.color
    else:
        crop = image
        ink_color = color
    if crop.size == 0:
        raise ValueError("椭圆章拉伸区域为空")
    mask = _ocr_color_mask(crop, ink_color)
    points = cv2.findNonZero(mask)
    if points is None:
        # Keep a useful visible intermediate even when the color is extremely
        # faint; the later OCR stage can then report a genuine empty result.
        normalized = crop.copy()
    else:
        bx, by, bw, bh = cv2.boundingRect(points)
        pad_x = max(3, round(bw * 0.035))
        pad_y = max(3, round(bh * 0.035))
        bx1, by1 = max(0, bx - pad_x), max(0, by - pad_y)
        bx2, by2 = min(crop.shape[1], bx + bw + pad_x), min(crop.shape[0], by + bh + pad_y)
        crop = crop[by1:by2, bx1:bx2]
        mask = mask[by1:by2, bx1:bx2]
        normalized = np.full_like(crop, 255)
        normalized[mask > 0] = crop[mask > 0]
    normalized_height, normalized_width = normalized.shape[:2]
    if normalized_height <= 0 or normalized_width <= 0:
        raise ValueError("椭圆章拉伸图片为空")
    side = max(normalized_height, normalized_width)
    normalized = cv2.resize(
        normalized,
        (side, side),
        interpolation=cv2.INTER_CUBIC,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", normalized)
    if not ok:
        raise ValueError("椭圆章拉伸图片编码失败")
    encoded.tofile(str(destination))
    return destination


def map_ellipse_box_to_normalized(
    source: str | Path,
    box: list[float] | tuple[float, float, float, float] | None,
    *,
    color: str = "red",
) -> list[float] | None:
    """Map a detector box from an oval crop into its square derivative.

    ``save_ellipse_normalized_seal`` trims the color-ink bounds and then
    stretches the shorter axis to a square.  Detector boxes are measured on
    the pre-normalized image, so reusing them directly would mask the wrong
    pixels.  Keep this transform beside the normalizer and clip the result to
    the generated image so callers can retain the existing safety gate.
    """
    if not box or len(box) != 4:
        return None
    image = _read_image(source)
    if image.size == 0:
        return None
    height, width = image.shape[:2]
    mask = _ocr_color_mask(image, color)
    points = cv2.findNonZero(mask)
    if points is None:
        crop_left, crop_top, crop_right, crop_bottom = 0, 0, width, height
    else:
        bx, by, bw, bh = cv2.boundingRect(points)
        pad_x = max(3, round(bw * 0.035))
        pad_y = max(3, round(bh * 0.035))
        crop_left = max(0, bx - pad_x)
        crop_top = max(0, by - pad_y)
        crop_right = min(width, bx + bw + pad_x)
        crop_bottom = min(height, by + bh + pad_y)
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    side = max(crop_width, crop_height)
    if crop_width <= 0 or crop_height <= 0 or side <= 0:
        return None
    scale_x = side / crop_width
    scale_y = side / crop_height
    left, top, right, bottom = [float(value) for value in box]
    mapped = [
        (left - crop_left) * scale_x,
        (top - crop_top) * scale_y,
        (right - crop_left) * scale_x,
        (bottom - crop_top) * scale_y,
    ]
    mapped_left = max(0.0, min(float(side), min(mapped[0], mapped[2])))
    mapped_top = max(0.0, min(float(side), min(mapped[1], mapped[3])))
    mapped_right = max(0.0, min(float(side), max(mapped[0], mapped[2])))
    mapped_bottom = max(0.0, min(float(side), max(mapped[1], mapped[3])))
    if mapped_right <= mapped_left or mapped_bottom <= mapped_top:
        return None
    return [mapped_left, mapped_top, mapped_right, mapped_bottom]


def _save_ellipse_annulus_unwrapped(
    gray: np.ndarray,
    mask: np.ndarray,
    destination: str | Path,
    *,
    single_line: bool = False,
) -> bool:
    """Unwrap a normalized oval's annulus with ellipse-aware sampling.

    ``warpPolar`` assumes both boundaries are concentric circles.  Even after
    an oval is stretched, the printed inner/outer borders are not perfectly
    concentric, so polar sampling creates the duplicated/wavy text seen in
    the v6 result.  Fit the two visible ellipse borders and sample between
    them directly instead.
    """
    # The stamp border is often broken by scan streaks and page text.  Fit
    # geometry on a lightly closed mask so those gaps do not make the outer
    # ellipse disappear; keep the original mask for the actual pixel sample.
    close_size = max(5, round(min(mask.shape[:2]) * 0.012))
    if close_size % 2 == 0:
        close_size += 1
    fit_mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size)),
    )
    contours, _ = cv2.findContours(fit_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    fitted = []
    for contour in contours:
        if len(contour) < 20 or cv2.contourArea(contour) < mask.size * 0.01:
            continue
        try:
            ellipse = cv2.fitEllipse(contour)
            (cx, cy), (axis_a, axis_b), angle = ellipse
            mean_axis = (float(axis_a) + float(axis_b)) / 2.0
            if mean_axis <= 0:
                continue
            points = contour[:, 0].astype(np.float32)
            theta = np.deg2rad(float(angle))
            dx, dy = points[:, 0] - cx, points[:, 1] - cy
            x_rot = dx * np.cos(theta) + dy * np.sin(theta)
            y_rot = -dx * np.sin(theta) + dy * np.cos(theta)
            radial_error = np.sqrt(
                (x_rot / (float(axis_a) / 2.0)) ** 2
                + (y_rot / (float(axis_b) / 2.0)) ** 2
            )
            fitted.append(
                (mean_axis, float(np.median(np.abs(radial_error - 1.0))), ellipse)
            )
        except (cv2.error, ValueError, ZeroDivisionError):
            continue
    if not fitted:
        return False
    # Reject contours that are mostly connected glyph blobs; real oval
    # borders have a much tighter ellipse residual.
    clean = [item for item in fitted if item[1] <= 0.06]
    if not clean:
        # A white-covered ring can leave only a broken outer contour.  Its
        # ellipse residual is naturally looser than a complete border, but
        # the largest contour is still useful for estimating the centre and
        # the text band.  Accept that relaxed fit only when there is no
        # reliable two-border fit; the ordinary path keeps its stricter gate.
        clean = [item for item in fitted if item[1] <= 0.12]
    if not clean:
        return False
    outer = max(clean, key=lambda item: item[0])
    inner_candidates = [
        item for item in clean
        if outer[0] * 0.35 < item[0] < outer[0] * 0.88
    ]
    estimated_inner = not inner_candidates
    if inner_candidates:
        inner = max(inner_candidates, key=lambda item: item[0])
    else:
        # Faint or damaged oval stamps often expose only one reliable border.
        # Keep the measured outer ellipse and estimate the inner edge from its
        # geometry instead of falling back to a circular warp.  The estimated
        # band is deliberately conservative: it excludes the outer border and
        # the centre logo, while retaining the company lettering between them.
        (outer_cx, outer_cy), (outer_a, outer_b), outer_angle = outer[2]
        inner = (
            outer[0] * 0.55,
            outer[1],
            ((outer_cx, outer_cy), (outer_a * 0.55, outer_b * 0.55), outer_angle),
        )
    (outer_cx, outer_cy), _, _ = outer[2]
    (inner_cx, inner_cy), _, _ = inner[2]
    center_x = (float(outer_cx) + float(inner_cx)) / 2.0
    center_y = (float(outer_cy) + float(inner_cy)) / 2.0
    width = gray.shape[1]
    output_width = max(1200, min(2200, width * 2))
    # Keep enough radial samples for thin Chinese strokes.  A 96-pixel strip
    # compresses the covered ring's text band and can erase the top/bottom of
    # characters such as “元”; the wider strip remains OCR-friendly after the
    # provider resizes it.
    output_height = max(180, round(output_width * 0.12))

    def radius(ellipse, angles):
        (_, _), (axis_a, axis_b), angle = ellipse
        radians = angles - np.deg2rad(float(angle))
        return 1.0 / np.sqrt(
            (np.cos(radians) / (float(axis_a) / 2.0)) ** 2
            + (np.sin(radians) / (float(axis_b) / 2.0)) ** 2
        )

    strips = []
    for phase in ((0.0,) if single_line else (0.0, 2.0 * np.pi / 3.0, 4.0 * np.pi / 3.0)):
        angles = np.linspace(
            np.pi / 2.0 + phase,
            np.pi / 2.0 + phase + 2.0 * np.pi,
            output_width,
            endpoint=False,
        )
        # With a covered type row there is no measured inner border.  Keep a
        # wider conservative band so the upper/lower strokes of the company
        # glyphs are not clipped by the estimated boundaries.  The centre
        # logo remains excluded because the estimated inner edge is still
        # well outside the middle of the stamp.
        outer_radius = radius(outer[2], angles) * (0.96 if estimated_inner else 0.98)
        inner_radius = radius(inner[2], angles) * (0.88 if estimated_inner else 1.08)
        fraction = np.linspace(0.0, 1.0, output_height, dtype=np.float32)[:, None]
        radial = outer_radius[None, :] * (1.0 - fraction) + inner_radius[None, :] * fraction
        map_x = (center_x + radial * np.cos(angles)[None, :]).astype(np.float32)
        map_y = (center_y + radial * np.sin(angles)[None, :]).astype(np.float32)
        strips.append(
            cv2.remap(
                gray,
                map_x,
                map_y,
                cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=255,
            )
        )
    gap = np.full((18, output_width), 255, dtype=np.uint8)
    output = strips[0] if single_line else np.vstack((strips[0], gap, strips[1], gap, strips[2]))
    # The visible outer border is sampled together with the text band.  After
    # unwrapping it becomes a long horizontal stroke; remove only strokes that
    # span a substantial part of the output so character components remain.
    unwrapped_ink = cv2.bitwise_not(output)
    border_lines = cv2.morphologyEx(
        unwrapped_ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(80, output.shape[1] // 5), 2)
        ),
    )
    border_lines = cv2.dilate(
        border_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5))
    )
    output = cv2.bitwise_not(cv2.subtract(unwrapped_ink, border_lines))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", output)
    if not ok:
        raise ValueError("椭圆环形文字展开图片编码失败")
    encoded.tofile(str(destination))
    return True
