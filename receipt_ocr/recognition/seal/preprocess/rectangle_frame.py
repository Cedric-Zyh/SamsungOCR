"""Measure rectangular rims and full-width rows without OCR-derived clipping."""
import cv2
import numpy as np

from .orientation_geometry import _rotation_geometry


def red_ink(image, threshold=25):
    color = image.astype(np.int16)
    return (color[:, :, 2] - np.maximum(color[:, :, 0], color[:, :, 1]) > threshold).astype(np.uint8)


def geometry_threshold(image):
    """Separate a pervasive pink wash only in geometry, retaining faint rims."""
    color = image.astype(np.int16)
    chroma = np.maximum(0, color[:, :, 2] - np.maximum(color[:, :, 0], color[:, :, 1])).astype(np.uint8)
    threshold, _ = cv2.threshold(chroma, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Normal pale stamps have mostly white paper. A high threshold there erases
    # their rims. Only a wash covering at least three quarters of the image
    # warrants separating the strong-ink population from the tinted paper.
    has_wash = np.mean(chroma > 25) > .75
    has_strong_ink = np.percentile(chroma, 95) >= 120
    return int(threshold) if threshold > 25 and has_wash and has_strong_ink else 25


def _runs(active):
    changes = np.diff(np.r_[0, active, 0].astype(int))
    return list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))


def _rim_angle(mask):
    height, width = mask.shape
    small = cv2.resize(mask.astype(np.float32), (500, max(1, round(height * 500 / width))),
                       interpolation=cv2.INTER_AREA)
    height, width = small.shape
    scores = []
    for angle in np.arange(-15, 15.001, .25):
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), float(angle), 1)
        rotated = cv2.warpAffine(small, matrix, (width, height), flags=cv2.INTER_LINEAR)
        profile = rotated[:, int(width * .22):int(width * .78)].mean(1)
        bands = (profile[:int(height * .28)], profile[int(height * .72):])
        score = sum(float(np.sum(band ** 4) / (np.sum(band) + 1e-6)) for band in bands)
        scores.append((score, float(angle)))
    return max(scores)[1]


def _side(profile, start, end, reverse=False):
    threshold = max(.50, float(np.max(profile[start:end])) * .75)
    active = (profile >= threshold).astype(np.uint8)[None, :]
    active = cv2.morphologyEx(active, cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8))[0]
    candidates = [(a, b) for a, b in _runs(active) if start <= (a + b) / 2 < end]
    if not candidates:
        raise ValueError("矩形边框连续性不足，使用文字框定位")
    return candidates[-1] if reverse else candidates[0]


def measure_frame(image):
    """Return one rotated full stamp and its measured interior; reject weak rims."""
    source_height, source_width = image.shape[:2]
    if min(source_height, source_width) < 30:
        raise ValueError("矩形边框图像过小")
    # Rim thickness/closing must mean the same thing at different input sizes.
    # Normalize only the geometry measurement; OCR rows retain source pixels.
    reference = image if source_width == 1000 else cv2.resize(
        image, (1000, round(source_height * 1000 / source_width)),
        interpolation=cv2.INTER_CUBIC if source_width < 1000 else cv2.INTER_AREA,
    )
    height, width = reference.shape[:2]
    threshold = geometry_threshold(reference)
    angle = _rim_angle(red_ink(reference, threshold))
    matrix, width, height = _rotation_geometry(width, height, angle)
    measured = cv2.warpAffine(reference, matrix, (width, height), flags=cv2.INTER_CUBIC,
                             borderValue=(255, 255, 255))
    mask = red_ink(measured, threshold)
    xp = mask[int(height * .25):int(height * .75)].mean(0)
    yp = mask[:, int(width * .22):int(width * .78)].mean(1)
    _, left_end = _side(xp, 0, int(width * .22))
    right_start, _ = _side(xp, int(width * .78), width, True)
    _, top_end = _side(yp, 0, int(height * .24))
    bottom_start, _ = _side(yp, int(height * .76), height, True)
    crop = [int(v) for v in (left_end + 1, top_end + 1, right_start - 1, bottom_start - 1)]
    left, top, right, bottom = crop
    if right - left < width * .6 or bottom - top < height * .5:
        raise ValueError("矩形内框范围过小")
    if source_width == 1000:
        rotated = measured
    else:
        matrix, output_width, output_height = _rotation_geometry(source_width, source_height, angle)
        rotated = cv2.warpAffine(image, matrix, (output_width, output_height), flags=cv2.INTER_CUBIC,
                                 borderValue=(255, 255, 255))
        scale = [output_width / width, output_height / height] * 2
        crop = np.rint(np.asarray(crop) * scale).astype(int).tolist()
    return rotated, crop, angle


def _ruled_cell_spans(mask):
    """Separate a measured grid by its rules, without treating rules as text."""
    height, width = mask.shape
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                             np.ones((1, max(3, round(width * .025))), np.uint8))
    rules = cv2.morphologyEx(joined, cv2.MORPH_OPEN,
                            np.ones((1, max(15, round(width * .65))), np.uint8))
    bands = [(a, b) for a, b in _runs(rules.mean(1) > .50)
             if a > height * .05 and b < height * .95 and b-a < height * .06]
    # One incidental long stroke is insufficient evidence of a grid.
    if len(bands) < 2:
        return []
    bounds = [(0, 0), *bands, (height, height)]
    margin = max(1, round(height * .003))
    spans = [(left[1] + (margin if left[1] else 0),
              right[0] - (margin if right[0] < height else 0))
             for left, right in zip(bounds, bounds[1:])]
    if any(b-a < max(10, height * .09) for a, b in spans):
        return []
    return [(int(a), int(b)) for a, b in spans
            if mask[a:b].sum() > width * max(2, height * .002)]


def row_spans(interior):
    """Find whitespace between rows, retaining the entire interior width."""
    threshold = geometry_threshold(interior)
    if threshold > 25:
        mask = red_ink(interior, threshold)
        height = mask.shape[0]
        # Close narrow rule-removal cracks in the measurement only. They are
        # internal breaks in letters, not gaps between complete text rows.
        joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                 np.ones((max(3, round(height * .025)), 1), np.uint8))
        smooth = cv2.GaussianBlur(joined.mean(1)[:, None], (1, 5), 0).ravel()
        gaps = [(a, b) for a, b in _runs(smooth < max(.015, float(smooth.max()) * .12))
                if b - a >= max(5, height * .025) and a > height * .04 and b < height * .96]
        bounds = [0] + [int((a + b) / 2) for a, b in gaps] + [height]
        return list(zip(bounds, bounds[1:]))
    mask = red_ink(interior, 8)
    height, width = mask.shape
    cells = _ruled_cell_spans(mask)
    if cells:
        return cells
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                  np.ones((1, max(15, int(width * .65))), np.uint8))
    # Remove rules only from the measurement, never from recognition pixels.
    measure = mask.copy()
    measure[horizontal > 0] = 0
    sigma = max(1, height * .006)
    radius = int(4 * sigma + .5)
    smooth = cv2.GaussianBlur(measure.mean(1)[:, None], (1, 2 * radius + 1),
                              sigmaX=0, sigmaY=sigma, borderType=cv2.BORDER_REFLECT)[:, 0]
    whitespace = smooth < max(.008, float(smooth.max()) * .10)
    gaps = [(a, b) for a, b in _runs(whitespace)
            if b - a >= max(3, height * .012) and a > height * .035 and b < height * .965]
    bounds = [0] + [int((a + b) / 2) for a, b in gaps] + [height]
    spans = [(int(a), int(b)) for a, b in zip(bounds, bounds[1:])
             if b - a >= height * .045 and measure[a:b].sum() > width * max(2, height * .002)]
    return [(a, b) for a, b in spans if not _edge_rule(mask, a, b)]


def _edge_rule(mask, top, bottom):
    """Reject a thin boundary fragment, never an interior separator/text row."""
    height, width = mask.shape
    if bottom - top > height * .08 or not (top == 0 or bottom == height):
        return False
    band = mask[top:bottom]
    rules = cv2.morphologyEx(band, cv2.MORPH_OPEN,
                             np.ones((1, max(15, round(width * .5))), np.uint8))
    return bool(rules.sum() / max(1, int(band.sum())) > .80)
