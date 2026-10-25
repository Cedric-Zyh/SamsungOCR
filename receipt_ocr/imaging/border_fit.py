"""Reject stray contour tails for geometry only; never alter source ink."""
from __future__ import annotations

import cv2
import numpy as np


def _coordinates(points, ellipse):
    (cx, cy), (a, b), angle = ellipse
    if min(a, b) <= 0 or not np.isfinite([cx, cy, a, b, angle]).all():
        raise ValueError("无效的印章边框")
    theta = np.deg2rad(angle)
    dx, dy = points[:, 0] - cx, points[:, 1] - cy
    return ((dx * np.cos(theta) + dy * np.sin(theta)) / (a / 2),
            (-dx * np.sin(theta) + dy * np.cos(theta)) / (b / 2))


def _errors(points, ellipse):
    x, y = _coordinates(points, ellipse)
    return np.abs(np.hypot(x, y) - 1)


def fit_border_contour(contour):
    """Keep the ordinary fit unless a well-supported robust fit is better.

    Detached lines are separate contours. A line touching the ring forms a
    long tail on the same contour, so fitting all its points shifts the centre.
    Iteratively exclude residual outliers, requiring a majority of the
    observed contour and broad angular support. Missing arcs and glyph blobs
    must not manufacture a new border. These masks affect geometry only.
    """
    points = np.asarray(contour, np.float32).reshape(-1, 2)
    original = cv2.fitEllipse(points)
    original_error = float(np.median(_errors(points, original)))
    if original_error < .025 or len(points) < 100:
        return original
    fitted = original
    # A long attached rule can bias the initial least-squares ellipse so much
    # that ordinary residual trimming keeps the wrong arc. Deterministic
    # minimal-subset proposals locate the majority-supported rim first.
    sample = points[::max(1, len(points) // 600)]
    best_error = float(np.median(_errors(sample, original)))
    rng = np.random.default_rng(0)
    for _ in range(128):
        try:
            candidate = cv2.fitEllipse(sample[rng.choice(len(sample), 6, replace=False)])
            (cx, cy), (a, b), _ = candidate
            if (not points[:, 0].min() <= cx <= points[:, 0].max()
                    or not points[:, 1].min() <= cy <= points[:, 1].max()
                    or min(a, b) <= 0 or max(a, b) > 2*np.linalg.norm(np.ptp(points, axis=0))):
                continue
            error = float(np.median(_errors(sample, candidate)))
            if error < best_error:
                fitted, best_error = candidate, error
        except (cv2.error, ValueError):
            continue
    for _ in range(8):
        errors = _errors(points, fitted)
        median = float(np.median(errors))
        mad = float(np.median(np.abs(errors - median)))
        keep = errors <= max(.015, median + 2.5 * mad)
        if keep.mean() < .55 or keep.sum() < 100:
            return original
        fitted = cv2.fitEllipse(points[keep])
    errors = _errors(points, fitted)
    if (float(np.median(errors)) >= min(.025, original_error * .65)
            or keep.mean() < .55):
        return original
    x, y = _coordinates(points[keep], fitted)
    bins = np.floor(np.mod(np.arctan2(y, x), 2*np.pi) * 72/(2*np.pi)).astype(int)
    covered = np.zeros(72, bool)
    covered[bins % 72] = True
    if covered.mean() < .80:
        return original
    longest = gap = 0
    for present in np.tile(covered, 2):
        gap = 0 if present else gap + 1
        longest = max(longest, gap)
    if longest > 14:
        return original
    return fitted
