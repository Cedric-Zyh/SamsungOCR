"""Select a separately verified Han company arc from a mixed-layout ring.

This is a conservative geometry candidate, not a general language detector.
Other scripts/digits are retained in a full-ring image but never sent to the
company recognizer. Ambiguous layouts keep the established ring input.
"""
from pathlib import Path
import re

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus, _ellipse_proportional_maps
from receipt_ocr.imaging.io import _read_image, _write_stage_image


def han_arc_bounds(strip):
    """Find a dominant tall-glyph run, retaining nearby detached strokes."""
    height, width = strip.shape
    if height < 100 or width < 6 * height:
        return None
    _, _, stats, _ = cv2.connectedComponentsWithStats((strip < 160).astype('uint8'))
    parts = [tuple(map(int, s)) for s in stats[1:] if s[4] > 40]
    seeds = sorted((s for s in parts if s[3] >= height * .45 and s[2] <= s[3] * 1.2))
    if len(seeds) < 6:
        return None
    glyph_height = float(np.median([s[3] for s in seeds]))
    groups = []
    for part in seeds:
        if groups and part[0] - max(s[0] + s[2] for s in groups[-1]) <= glyph_height:
            groups[-1].append(part)
        else:
            groups.append([part])
    groups.sort(key=len, reverse=True)
    if len(groups[0]) < 6 or (len(groups) > 1 and len(groups[1]) >= len(groups[0]) * .5):
        return None
    chosen = groups[0]
    left, right = min(s[0] for s in chosen), max(s[0] + s[2] for s in chosen)

    def complete_column(part):
        # Several short strokes may make one full-height Han glyph. Do not
        # extend the run for isolated cursive stems or dots at its boundary.
        column = [p for p in parts if p[2] <= glyph_height * 1.25
                  and p[3] <= glyph_height * 1.4
                  and min(p[0] + p[2], part[0] + part[2]) - max(p[0], part[0])
                  >= .4 * min(p[2], part[2])
                  and abs((p[1] + p[3]/2) - (part[1] + part[3]/2)) <= glyph_height]
        return bool(column) and max(p[1] + p[3] for p in column) - min(p[1] for p in column) >= glyph_height * .90

    while True:
        # Short parts can be an entire edge glyph (乌/司), not just noise.
        nearby = [s for s in parts if s[3] >= glyph_height * .40 and s[2] <= s[3] * 1.25
                  and s[0] <= right + glyph_height * .30
                  and s[0] + s[2] >= left - glyph_height * .30
                  and (left <= s[0] and s[0] + s[2] <= right or complete_column(s))]
        new_left, new_right = min(s[0] for s in nearby), max(s[0] + s[2] for s in nearby)
        if (new_left, new_right) == (left, right):
            break
        left, right = new_left, new_right
    if not .22 * width < right - left < .65 * width:
        return None
    # Require a distinct outside run of connected, non-square glyphs. A
    # company arc followed only by small serial digits is not this layout.
    outside = [s for s in parts if (s[0] + s[2] < left or s[0] > right)
               and s[3] >= glyph_height * .55 and s[2] > s[3] * 1.3]
    if len(outside) < 2:
        return None
    top = min(s[1] for s in nearby)
    bottom = max(s[1] + s[3] for s in nearby)
    pad = max(8, round(glyph_height * .10))
    # Never select a run cut by the periodic seam; incomplete names must not
    # be silently promoted based on high OCR confidence alone.
    if left < pad or right > width - pad:
        return None
    # Context padding must stop in the gap rather than reintroduce fragments
    # of the neighboring script that the geometry selection just excluded.
    previous_end = max((s[0] + s[2] for s in parts if s[0] + s[2] <= left), default=0)
    next_start = min((s[0] for s in parts if s[0] >= right), default=width)
    left_pad = min(pad, (left - previous_end) // 2)
    right_pad = min(pad, (next_start - right) // 2)
    return {'box': [left - left_pad, max(0, top - pad), right + right_pad, min(height, bottom + pad)],
            'glyph_height': round(glyph_height, 2), 'seed_count': len(chosen),
            'outside_wide_components': len(outside)}


def prepare_han_ring(source, directory, index, *, reader, provider, color='red'):
    if color != 'red':
        return None
    image = _read_image(source)
    geometry = _fit_ellipse_annulus(_ocr_color_mask(image, color))
    if geometry is None:
        return None
    outer, inner, _ = geometry
    axis_a, axis_b = outer[2][1]
    if outer[1] > .06 or min(axis_a, axis_b) / max(axis_a, axis_b) < .80:
        return None
    # Trust the outer frame, not an inner ellipse fitted to letters or a logo.
    map_x, map_y = _ellipse_proportional_maps(outer, inner, True)
    blue, green, red = [v.astype('float32') for v in cv2.split(image)]
    chroma = red - np.maximum(blue, green)

    def sample(gain):
        gray = (255 - np.clip(chroma * gain, 0, 255)).astype('uint8')
        return cv2.remap(gray, map_x, map_y, cv2.INTER_CUBIC,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=255)

    full = sample(1.3)
    selection = han_arc_bounds(full)
    if selection is None:
        return None
    left, top, right, bottom = selection['box']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    def save(name, pixels):
        path = directory / f'seal-{index}-han-{name}.png'
        _write_stage_image(path, pixels, '.png')
        return path

    def line(strip):
        return cv2.copyMakeBorder(strip[top:bottom, left:right], 16, 16, 16, 16,
                                  cv2.BORDER_CONSTANT, value=255)

    selected = save('line', line(full))
    check = save('check', line(sample(1.0)))
    verified = []
    for path in [selected, check]:
        reads = reader.read_line(path, provider=provider)
        if len(reads) != 1:
            return None
        text = reads[0].text.strip()
        score = float(reads[0].confidence)
        # Validate only an independently read company phrase. No requirement,
        # expected name, character replacement or suffix completion is used.
        if (not np.isfinite(score) or score < .95
                or not re.fullmatch(r'[\u4e00-\u9fff]{6,}(?:有限公司|有限责任公司)', text)):
            return None
        verified.append({'text': text, 'confidence': score})
    if verified[0]['text'] != verified[1]['text']:
        return None
    full_path = save('full', full)
    polygon = np.rint(np.concatenate([
        np.column_stack((map_x[0, left:right], map_y[0, left:right])),
        np.column_stack((map_x[-1, left:right][::-1], map_y[-1, left:right][::-1])),
    ])).astype('int32')
    coverage = np.zeros(image.shape[:2], np.uint8)
    cv2.fillPoly(coverage, [polygon], 255)
    band = np.full_like(image, 255)
    band[coverage > 0] = image[coverage > 0]
    return {'ring': selected, 'full': full_path, 'band': save('band', band),
            'evidence': {'method': 'verified_han_company_arc', **selection,
                         'source_size': [image.shape[1], image.shape[0]],
                         'full_size': [full.shape[1], full.shape[0]],
                         'reads': verified, 'gains': [1.3, 1.0],
                         'geometry': 'outer_frame_proportional_full_depth'}}
