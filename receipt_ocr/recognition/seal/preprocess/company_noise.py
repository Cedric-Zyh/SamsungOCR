"""Image-only terminal-cloud proposal for continuous company-name strips.

No OCR, expected labels, company names, or character dictionaries are used.
Rejected proposals always return the full original bounds. Bounds follow PIL's
half-open convention: [left, top, right, bottom]. A proposal still requires
independent OCR agreement before it can replace a recognition result.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return [(int(s), int(e)) for s, e in zip(edges[::2], edges[1::2])]


def _terminal_glyph_support(core, segment, median_width, median_height, min_area, *, left):
    """Protect tall glyph strokes even when close spacing joins their columns."""
    pixels = core[:, segment['x0']:segment['x1']].astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(pixels, 8)
    stats = stats[1:]
    # Long narrow strokes and ordinary upright glyphs retain character-scale
    # height. A tall, several-character-wide connected noise mass is different.
    tall = stats[(stats[:, cv2.CC_STAT_AREA] >= min_area)
                 & (stats[:, cv2.CC_STAT_HEIGHT] >= .60 * median_height)
                 & (stats[:, cv2.CC_STAT_WIDTH] <= 1.5 * median_width)]
    if left:
        inner = tall[:, cv2.CC_STAT_LEFT] + tall[:, cv2.CC_STAT_WIDTH] > segment['width'] - median_width
    else:
        inner = tall[:, cv2.CC_STAT_LEFT] < median_width
    count, inner_count = len(tall), int(np.count_nonzero(inner))
    return {'tall_components': count, 'inner_tall_components': inner_count,
            'protected': count >= 2 or inner_count > 0}


def _probe(gray, threshold):
    height, width = gray.shape
    ink = gray < threshold
    top, bottom = round(.05 * height), round(.84 * height)
    projection = ink[top:bottom].mean(axis=0)
    gaps = [(s, e) for s, e in _runs(projection <= .01)
            if e - s >= max(3, round(.045 * height))]
    cuts = [0] + [(s + e) // 2 for s, e in gaps] + [width]
    segments = []
    min_area = max(20, height * height * .0008)
    for left, right in zip(cuts[:-1], cuts[1:]):
        ys, xs = np.where(ink[top:bottom, left:right])
        if not len(xs):
            continue
        x0, x1 = int(xs.min() + left), int(xs.max() + left + 1)
        y0, y1 = int(ys.min() + top), int(ys.max() + top + 1)
        _, _, stats, _ = cv2.connectedComponentsWithStats(
            ink[y0:y1, x0:x1].astype(np.uint8), 8)
        components = int(np.sum(stats[1:, cv2.CC_STAT_AREA] >= min_area))
        segments.append(dict(left=left, right=right, x0=x0, x1=x1,
                             width=x1-x0, height=y1-y0, cc=components))
    typical = [s for s in segments
               if s['height'] >= .5 * height and .15 * height <= s['width'] <= 1.5 * height]
    if len(typical) < 6:
        return {'ok': False, 'reason': 'insufficient_text_segments',
                'threshold': int(threshold), 'typical_count': len(typical)}
    median_width = float(np.median([s['width'] for s in typical]))
    median_height = float(np.median([s['height'] for s in typical]))
    median_cc = float(np.median([s['cc'] for s in typical]))

    def is_cloud(segment):
        return (segment['width'] > 2.8 * median_width
                and segment['cc'] > 3 * max(median_cc, 1))

    # Only inspect the outermost nonblank segment on each side. Never discard
    # an ordinary narrow segment to reach a noisy segment farther inward.
    left_candidate = is_cloud(segments[0])
    right_candidate = is_cloud(segments[-1])
    support = [
        _terminal_glyph_support(ink[top:bottom], segment, median_width, median_height,
                                min_area, left=left)
        if candidate else None
        for segment, candidate, left in ((segments[0], left_candidate, True),
                                        (segments[-1], right_candidate, False))
    ]
    left_cloud, right_cloud = left_candidate, right_candidate
    details = dict(threshold=int(threshold), median_glyph_width=median_width,
                   median_glyph_height=median_height, median_cc=median_cc,
                   typical_count=len(typical), terminal_glyph_support=support,
                   left_cloud=left_cloud, right_cloud=right_cloud,
                   edge_segments=[segments[0], segments[-1]])
    # Reject the whole proposal when either terminal candidate has glyph
    # support. Suppressing that side alone could hide threshold disagreement.
    if any(s and s['protected'] for s in support):
        return {'ok': False, 'reason': 'terminal_glyph_support', **details}
    if not (left_cloud or right_cloud):
        return {'ok': False, 'reason': 'no_terminal_cloud', **details}
    if len(segments) - int(left_cloud) - int(right_cloud) < 6:
        return {'ok': False, 'reason': 'insufficient_retained_segments', **details}
    proposed_left = segments[1]['left'] if left_cloud else 0
    proposed_right = segments[-2]['right'] if right_cloud else width

    # Demand a genuine full-height zero-ink band, not merely a low core count.
    full_gaps = [(s, e) for s, e in _runs(~ink.any(axis=0))
                 if e-s >= max(4, math.ceil(.025 * height))]

    def snap(x):
        choices = [(s, e) for s, e in full_gaps
                   if abs((s+e)/2-x) <= .18*height]
        return min(choices, key=lambda z: abs((z[0]+z[1])/2-x)) if choices else None

    left_gap = snap(proposed_left) if left_cloud else None
    right_gap = snap(proposed_right) if right_cloud else None
    details.update(full_height_gaps=[left_gap, right_gap])
    if (left_cloud and left_gap is None) or (right_cloud and right_gap is None):
        return {'ok': False, 'reason': 'missing_full_height_gap', **details}
    left = (left_gap[0]+left_gap[1])//2 if left_cloud else 0
    right = (right_gap[0]+right_gap[1])//2 if right_cloud else width
    if right-left < .5*width:
        return {'ok': False, 'reason': 'excessive_crop', **details}
    return {'ok': True, 'bounds': [left, 0, right, height], **details}


def propose_company_noise_trim(gray):
    """Return accepted, bounds, and pixel measurements; never mutate input."""
    gray = np.asarray(gray)
    if gray.ndim != 2 or gray.dtype != np.uint8 or not gray.size:
        raise ValueError('Expected a nonempty 2D uint8 grayscale image')
    height, width = gray.shape
    original = [0, 0, width, height]
    if height < 32 or width < 5*height:
        return {'accepted': False, 'bounds': original,
                'measurements': {'reason': 'unsupported_strip_geometry'}}
    threshold, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    thresholds = [max(1, int(threshold)-15), int(threshold), min(254, int(threshold)+15)]
    probes = [_probe(gray, t) for t in thresholds]
    measurements = {'otsu': int(threshold), 'probes': probes}
    if not all(p['ok'] for p in probes):
        measurements['reason'] = next(p['reason'] for p in probes if not p['ok'])
        return {'accepted': False, 'bounds': original, 'measurements': measurements}
    side_flags = {(p['left_cloud'], p['right_cloud']) for p in probes}
    if len(side_flags) != 1:
        measurements['reason'] = 'unstable_cloud_sides'
        return {'accepted': False, 'bounds': original, 'measurements': measurements}
    tolerance = max(3, round(.06*height))
    spreads = [max(p['bounds'][i] for p in probes)-min(p['bounds'][i] for p in probes)
               for i in (0, 2)]
    measurements.update(boundary_spreads=spreads, stability_tolerance=tolerance)
    if max(spreads) > tolerance:
        measurements['reason'] = 'unstable_threshold_boundaries'
        return {'accepted': False, 'bounds': original, 'measurements': measurements}
    measurements['reason'] = 'terminal_cloud_with_stable_full_height_gap'
    return {'accepted': True, 'bounds': probes[1]['bounds'], 'measurements': measurements}
