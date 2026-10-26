"""Ellipse normalization and annulus sampling."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from .contracts import SealRegion
from .io import _read_image
from .colors import _ocr_color_mask
from .border_fit import fit_border_contour


def _ellipse_fits_canvas(ellipse, shape):
    """Reject extrapolated borders that extend well outside the stamp crop."""
    (cx, cy), (a, b), angle = ellipse
    height, width = shape[:2]
    theta = np.deg2rad(angle)
    rx = np.hypot(a * np.cos(theta), b * np.sin(theta)) / 2
    ry = np.hypot(a * np.sin(theta), b * np.cos(theta)) / 2
    return bool(a > 0 and b > 0
                and cx - rx >= -width * .10 and cx + rx <= width * 1.10
                and cy - ry >= -height * .10 and cy + ry <= height * 1.10)


def _concentric_inner(inner, outer):
    return np.linalg.norm(np.subtract(inner[2][0], outer[2][0])) <= outer[0] * .08


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


def _fit_ellipse_annulus(mask, *, reject_outliers=True):
    """Share the measured ring geometry between the preview and OCR input."""
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
            # Geometry must not follow a scan rule attached outside the rim.
            # The original mask is retained unchanged for actual OCR sampling.
            ellipse = (fit_border_contour(contour) if reject_outliers
                       and cv2.contourArea(contour) >= mask.size * .12
                       else cv2.fitEllipse(contour))
            if not _ellipse_fits_canvas(ellipse, mask.shape):
                continue
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
        return None
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
        return None
    outer = max(clean, key=lambda item: item[0])
    inner_candidates = [
        item for item in clean
        if outer[0] * 0.35 < item[0] < outer[0] * 0.88
        and _concentric_inner(item, outer)
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
    return outer, inner, estimated_inner


def _fit_unfiltered_ellipse_annulus(mask):
    """Raw reference residual for the separately verified strong-ink fallback.

    It compares two thresholded geometry masks with the polluted reference;
    its actual sampling never uses this reference ellipse.
    """
    return _fit_ellipse_annulus(mask, reject_outliers=False)


def _ellipse_normal_maps(outer, inner, estimated_inner):
    """Sample equal arc distances, with upright rows along border normals.

    Rays from the centre shear characters on an ellipse. Normals are locally
    perpendicular to the text baseline; equal arc distances also avoid
    squeezing characters near the ends of the major axis.
    """
    (cx, cy), (axis_a, axis_b), angle = outer[2]
    a, b = axis_a / 2, axis_b / 2
    rotation = np.deg2rad(angle)
    co, si = np.cos(rotation), np.sin(rotation)
    start = np.arctan2(co / b, si / a)  # seam on the bottom ray
    theta = np.linspace(start, start + 2 * np.pi, 12001)
    x = cx + a * np.cos(theta) * co - b * np.sin(theta) * si
    y = cy + a * np.cos(theta) * si + b * np.sin(theta) * co
    distance = np.r_[0.0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
    width = max(1200, min(3600, round(distance[-1])))
    theta = np.interp(np.linspace(0, distance[-1], width, endpoint=False), distance, theta)
    x = cx + a * np.cos(theta) * co - b * np.sin(theta) * si
    y = cy + a * np.cos(theta) * si + b * np.sin(theta) * co
    nx = np.cos(theta) / a * co - np.sin(theta) / b * si
    ny = np.cos(theta) / a * si + np.sin(theta) / b * co
    norm = np.hypot(nx, ny)
    nx, ny = nx / norm, ny / norm
    begin = min(a, b) * .05
    end = min(a, b) * .55 if estimated_inner else max(begin + 1, (outer[0] - inner[0]) / 2 * .92)
    # Avoid intersecting normals at the narrow tips of a very elongated oval.
    end = min(end, .85 * min(a, b) ** 2 / max(a, b))
    height = max(96, round((end - begin) * width / distance[-1]))
    depth = np.linspace(begin, end, height)[:, None]
    return ((x - depth * nx).astype(np.float32),
            (y - depth * ny).astype(np.float32))


def _ellipse_proportional_maps(outer, inner, estimated_inner):
    """Unwrap one revolution in ellipse coordinates without crossing normals.

    Retain a full-height proportional band even at the major-axis tips.
    Normal-offset sampling has to cap its depth there to prevent folds,
    which clips tall side glyphs. The source image is never stretched first.
    """
    (cx, cy), (axis_a, axis_b), angle = outer[2]
    a, b = axis_a / 2, axis_b / 2
    rotation = np.deg2rad(angle)
    co, si = np.cos(rotation), np.sin(rotation)
    start = np.arctan2(co / b, si / a)  # bottom ray, independent of axis order
    width = max(1200, min(3600, round(np.pi * (a + b))))
    theta = np.linspace(start, start + 2 * np.pi, width, endpoint=False)
    outer_fraction = .91
    inner_fraction = .40 if estimated_inner else min(.40, inner[0] / outer[0])
    height = max(96, round(width * (outer_fraction - inner_fraction) / (2 * np.pi)))
    fraction = np.linspace(outer_fraction, inner_fraction, height)[:, None]
    x = a * np.cos(theta) * fraction
    y = b * np.sin(theta) * fraction
    return ((cx + x * co - y * si).astype(np.float32),
            (cy + x * si + y * co).astype(np.float32))


def _ring_text_normal_maps(mask, geometry):
    """Reject an inferred inner contour when it cuts through several glyphs.

    Closed company strokes can resemble a concentric inner border. A normal
    offset limited by that contour clips their inward-facing ends. Inspect
    only image evidence, before OCR, and reuse the wider single-border band
    only for near-round seals with at least three substantial cut components.
    Explicit border guides and proportional oval sampling bypass this helper.
    """
    outer, inner, estimated_inner = geometry
    maps = _ellipse_normal_maps(*geometry)
    axes = outer[2][1]
    if estimated_inner or max(axes) > min(axes) * 1.25:
        return maps
    sampled = cv2.remap(mask, *maps, cv2.INTER_NEAREST,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    height, width = sampled.shape
    _, _, stats, _ = cv2.connectedComponentsWithStats((sampled > 0).astype(np.uint8))
    cut_components = sum(
        top + h >= height - 1
        and h >= height * .35
        and 8 <= w <= width * .12
        and area >= max(25, height)
        for left, top, w, h, area in stats[1:]
    )
    if cut_components < 3:
        return maps
    wider = _ellipse_normal_maps(outer, inner, True)
    # A genuinely deep measured band needs no replacement by a shallower one.
    old_depth = np.hypot(maps[0][-1] - maps[0][0], maps[1][-1] - maps[1][0])
    new_depth = np.hypot(wider[0][-1] - wider[0][0], wider[1][-1] - wider[1][0])
    return wider if np.median(new_depth - old_depth) > 1 else maps


def _split_short_seam_strokes(image):
    """Find matching short strokes on both ends, excluding the outer rim."""
    height, width = image.shape
    _, _, stats, _ = cv2.connectedComponentsWithStats((image < 225).astype(np.uint8))
    short = [s for s in stats[1:] if s[4] >= 10
             and height * .08 <= s[1] and s[1] + s[3] <= height * .95
             and s[3] < height * .20 and s[2] >= height * .06]
    left = [s for s in short if s[0] <= 2]
    right = [s for s in short if s[0] + s[2] >= width - 2]
    return any(min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
               >= .5 * min(a[3], b[3]) for a in left for b in right)


def _align_complete_ring_seam(image):
    """Move a cut glyph into the interior before trimming a complete revolution.

    Unlike the later wide-gap heuristic (which also accepts partial arcs),
    this runs only on the untrimmed periodic sampler output. Require ink at
    both ends and a nearly empty gap across the *entire* radial band, so an
    upper stroke or a small serial digit cannot be mistaken for whitespace.
    Rolling keeps every pixel, including faint strokes and gap speckles.
    """
    height, width = image.shape
    if height < 20 or width < height * 3:
        return image
    if not _split_short_seam_strokes(image):
        return image
    ink = image < 225
    edge = max(2, round(height * .025))
    if not (ink[:, :edge].sum() >= height * .08
            and ink[:, -edge:].sum() >= height * .08):
        return image
    sparse = ink.sum(axis=0) <= max(1, int(height * .008))
    best_start, best_width, start = 0, 0, None
    for index, blank in enumerate(np.append(sparse, False)):
        if blank and start is None:
            start = index
        elif not blank and start is not None:
            length = index - start
            if length > best_width:
                best_start, best_width = start, length
            start = None
    if best_width < max(4, round(height * .05)):
        return image
    seam = best_start + best_width // 2
    return np.roll(image, -seam, axis=1)


def _trim_ring_whitespace(image, *, preserve_short_strokes=False):
    """Trim only outside the text span, retaining every pixel inside it."""
    height, width = image.shape
    _, _, stats, _ = cv2.connectedComponentsWithStats((image < 225).astype(np.uint8))
    components = [s for s in stats[1:]
                  if s[cv2.CC_STAT_AREA] >= 10 and s[cv2.CC_STAT_HEIGHT] >= height * .20]
    if preserve_short_strokes:
        components.extend(s for s in stats[1:] if s[4] >= 10
                          and height * .08 <= s[1] and s[1] + s[3] <= height * .95
                          and s[3] < height * .20 and s[2] >= height * .06)
    # Letters on the outer arc can touch the sampled strip's top edge.
    # Position alone cannot distinguish them from border ink (long border
    # strokes are already removed before trimming). Rejecting that edge
    # used to crop away the beginning of a company name on tilted ovals.
    if len(components) < 3:
        return image
    left = min(s[0] for s in components)
    right = max(s[0] + s[2] for s in components)
    # If no safe seam was found, retain the periodic ends instead of
    # discarding the two halves of a short stroke (e.g. the character 二).
    if _split_short_seam_strokes(image):
        left, right = 0, width
    top = min(s[1] for s in components)
    bottom = max(s[1] + s[3] for s in components)
    # Include faint/short fragments adjacent to the strong character bodies.
    pad = max(15, round(height * .10))
    return image[max(0, top - 8):min(height, bottom + 8),
                 max(0, left - pad):min(width, right + pad)]


def _save_ellipse_annulus_unwrapped(
    gray: np.ndarray,
    mask: np.ndarray,
    destination: str | Path,
    *,
    single_line: bool = False,
    ring_guide=None,
    trim_whitespace: bool = True,
    proportional: bool = False,
) -> bool:
    """Use a measured border, or the selected fitted-ellipse sampler."""
    if ring_guide is not None and tuple(gray.shape[:2]) != ring_guide.source_shape:
        raise ValueError("印章边框与展开图片坐标不一致")
    geometry = ring_guide.annulus if ring_guide is not None else _fit_ellipse_annulus(mask)
    if geometry is None:
        return False
    outer, inner, estimated_inner = geometry
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
        if single_line:
            if ring_guide is not None:
                map_x, map_y = ring_guide.map_x, ring_guide.map_y
            elif proportional:
                map_x, map_y = _ellipse_proportional_maps(*geometry)
            else:
                map_x, map_y = _ring_text_normal_maps(mask, geometry)
            strips.append(cv2.remap(gray, map_x, map_y, cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=255))
            break
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
    if single_line and trim_whitespace:
        aligned = _align_complete_ring_seam(output)
        output = _trim_ring_whitespace(aligned, preserve_short_strokes=aligned is not output)
    if single_line and proportional:
        output = cv2.copyMakeBorder(output, 16, 16, 16, 16,
                                    cv2.BORDER_CONSTANT, value=255)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", output)
    if not ok:
        raise ValueError("椭圆环形文字展开图片编码失败")
    encoded.tofile(str(destination))
    return True
