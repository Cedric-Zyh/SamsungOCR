"""Fit a smooth company-ring reference from reliable parts of the outer rim.

This changes only the sampling reference for a continuous company arc. The
measured border, source image and independently directed lower rows stay intact.
"""
import cv2
import numpy as np

from receipt_ocr.imaging.border_fit import _coordinates, _errors


def company_border_points(guide):
    points = guide.points
    if guide.summary().get('method') != 'observed_ellipse_border':
        return points, None
    if len(points) < 72 or not np.isfinite(points).all():
        return points, None
    (cx, cy), (a, b), angle = guide.annulus[0][2]
    if min(a, b) <= 0:
        return points, None
    x, y = _coordinates(points, guide.annulus[0][2])
    radius = np.hypot(x, y)
    median = float(np.median(radius))
    deviation = float(np.median(np.abs(radius - median)))
    supported = np.asarray(getattr(guide, 'supported', np.ones(len(points), bool)), bool)
    if supported.shape != radius.shape:
        return points, None
    # Do not fit the inward detour through glyphs as part of the rim. The
    # remaining points must independently support a full ellipse on all sides.
    keep = supported & (np.abs(radius - median) <= max(.03, 3 * deviation))
    if keep.mean() < .55:
        return points, None
    try:
        ellipse = cv2.fitEllipse(points[keep].astype('float32'))
        for _ in range(5):
            errors = _errors(points, ellipse)
            middle = float(np.median(errors))
            spread = float(np.median(np.abs(errors - middle)))
            keep = supported & (errors <= max(.015, middle + 2.5 * spread))
            if keep.mean() < .55:
                return points, None
            ellipse = cv2.fitEllipse(points[keep].astype('float32'))
    except (cv2.error, ValueError):
        return points, None
    (fx, fy), (fa, fb), fitted_angle = ellipse
    if (not np.isfinite([fx, fy, fa, fb, fitted_angle]).all() or min(fa, fb) <= 0
            or np.linalg.norm([fx - cx, fy - cy]) > min(a, b) * .12
            or not .75 <= min(fa, fb) / min(a, b) <= 1.25
            or not .75 <= max(fa, fb) / max(a, b) <= 1.25):
        return points, None
    residual = float(np.median(_errors(points[keep], ellipse)))
    x, y = _coordinates(points[keep], ellipse)
    covered = np.zeros(72, bool)
    bins = (np.mod(np.arctan2(y, x), 2 * np.pi) * 72 / (2 * np.pi)).astype(int)
    covered[bins % 72] = True
    longest = gap = 0
    for present in np.tile(covered, 2):
        gap = 0 if present else gap + 1
        longest = max(longest, gap)
    if residual > .02 or covered.mean() < .65 or longest > 18:
        return points, None
    # Missing rim segments are completed by this ellipse, never by following
    # nearby text. Sampling must also use its centre and radial coordinates.
    theta = np.linspace(0, 2 * np.pi, len(points), endpoint=False)
    turn = np.deg2rad(fitted_angle)
    corrected = np.column_stack((
        fx + fa / 2 * np.cos(theta) * np.cos(turn) - fb / 2 * np.sin(theta) * np.sin(turn),
        fy + fa / 2 * np.cos(theta) * np.sin(turn) + fb / 2 * np.sin(theta) * np.cos(turn),
    ))
    return corrected, {
        'method': 'robust_company_ellipse',
        'ellipse': ellipse,
        'supported_fraction': float(keep.mean()),
        'angular_coverage': float(covered.mean()),
        'residual': residual,
    }
