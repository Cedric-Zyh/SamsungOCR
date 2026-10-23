"""One forward company arc after independently checking the lower reading.

The inner purpose and number have already been located and masked. A reliable
lower text line keeps its own direction. Otherwise the horizontal centre must
not divide the first/last company glyphs into a backwards lower arc.
"""
from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border
from receipt_ocr.providers.paddle_runtime import variant_of
from .detected_layout import fitted_guide


def continuous_company_eligible(artifact):
    layout = artifact.get('multi_zone_layout') or {}
    if (layout.get('method') != 'ellipse_detected_rows'
            or layout.get('expected_inner_roles') != ['type', 'number']
            or set(layout.get('arcs', {})) != {'upper', 'lower'}):
        return False
    # Unknown but confident lower words are also protected, not just phones
    # that fit a particular telephone regex. Failed/empty lower OCR can retry.
    inputs = [row for row in artifact.get('inputs', []) if row.get('channel') == 'layout_lower']
    if not inputs or any(row.get('status') not in {'completed', 'empty'} for row in inputs):
        return False
    return not any(row.get('channel') == 'layout_lower' and row.get('text')
                   and float(row.get('confidence') or 0) >= .80
                   for row in artifact.get('reads', []))


def _forward_strip(image, guide, start, end, inner, outer, color):
    (cx, cy), _, _ = guide.annulus[0][2]
    points = guide.points
    angles = np.mod(np.arctan2(points[:, 1]-cy, points[:, 0]-cx), 2*np.pi)
    order = np.argsort(angles)
    angles, points = angles[order], points[order]
    aa = np.r_[angles[-1]-2*np.pi, angles, angles[0]+2*np.pi]
    xx = np.r_[points[-1, 0], points[:, 0], points[0, 0]]
    yy = np.r_[points[-1, 1], points[:, 1], points[0, 1]]
    theta = np.mod(np.deg2rad(np.linspace(start, end, 2400)), 2*np.pi)
    bx, by = np.interp(theta, aa, xx), np.interp(theta, aa, yy)
    distance = np.r_[0, np.cumsum(np.hypot(np.diff(bx), np.diff(by)))]
    samples = np.linspace(0, distance[-1], max(1, round(distance[-1])))
    bx, by = np.interp(samples, distance, bx), np.interp(samples, distance, by)
    radial = np.linspace(outer, inner, 200)
    mx = (cx+(bx-cx)*radial[:, None]).astype('float32')
    my = (cy+(by-cy)*radial[:, None]).astype('float32')
    blue, green, red = (c.astype(float) for c in cv2.split(image))
    chroma = red-np.maximum(blue, green) if color == 'red' else blue-np.maximum(red, green)
    gray = (255-np.clip(chroma*255/160, 0, 255)).astype('uint8')
    return cv2.remap(gray, mx, my, cv2.INTER_CUBIC,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def continuous_company_pixels(image, guide, *, color='red'):
    # Track the outside of the text rather than the dense security pattern
    # inside it. One half-degree bin, with two-degree closing of stroke holes.
    tracking = _forward_strip(image, guide, 90, 450, .80, .90, color)
    small = cv2.resize(tracking, (720, 200), interpolation=cv2.INTER_AREA)
    support = (np.mean(small < 210, axis=0) >= .065).astype('uint8')
    support = cv2.morphologyEx(support[None, :], cv2.MORPH_CLOSE,
                              np.ones((1, 5), np.uint8))[0]
    empty = np.r_[False, support == 0, False].astype(int)
    starts = np.where(np.diff(empty) == 1)[0]
    ends = np.where(np.diff(empty) == -1)[0]
    gaps = [(90+left/2, 90+right/2) for left, right in zip(starts, ends)
            if right-left >= 20]
    left = [gap for gap in gaps if gap[1] <= 245 and gap[0] < 210]
    right = [gap for gap in gaps if gap[0] >= 295 and gap[1] > 330]
    # The widest separator is safer than the nearest small gap: faint glyphs
    # must not be mistaken for the end of the company name.
    start = max(left, key=lambda gap: gap[1]-gap[0])[1]-3 if left else 90
    end = max(right, key=lambda gap: gap[1]-gap[0])[0]+3 if right else 450
    pixels = _forward_strip(image, guide, start, end, .60, .94, color)
    pixels = cv2.copyMakeBorder(pixels, 12, 12, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return pixels, {'method': 'forward_border_company_v1', 'degrees': [start, end],
                    'radial_range': [.60, .94], 'tracking_radial_range': [.80, .90],
                    'gaps': gaps, 'border': guide.summary()}


def read_continuous_company(artifact, directory, index, prefix, *, reader, provider):
    """Adopt one fixed image-derived reading, retaining the split raw evidence.

    No expected wording is accepted by this function. Changes to the artifact
    are committed only after sampling and OCR complete successfully.
    """
    if variant_of(provider) != 'v6' or not continuous_company_eligible(artifact):
        return False
    directory = Path(directory)
    source_url = artifact.get('ring_input_source_url') or artifact.get('body_url')
    masked_url = artifact.get('ring_input_url')
    source_name = artifact.get('ring_input_source_name') or Path(source_url or '').name
    masked_name = artifact.get('ring_input_name') or Path(masked_url or '').name
    if not source_name or not masked_name:
        return False
    source = directory / Path(source_name).name
    masked = directory / Path(masked_name).name
    intact, image = cv2.imread(str(source)), cv2.imread(str(masked))
    if intact is None or image is None or image.shape != intact.shape:
        return False
    color = artifact.get('color', 'red')
    guide = trace_ellipse_border(source, color=color) or fitted_guide(intact, color=color)
    if guide is None:
        return False
    pixels, geometry = continuous_company_pixels(image, guide, color=color)
    destination = directory / f'seal-{index}-zones-company-continuous.png'
    if not cv2.imwrite(str(destination), pixels):
        raise ValueError('连续公司环字图片保存失败')
    observations = [row for row in reader.read_line(destination, provider=provider) if row.text]
    if len(observations) != 1:
        return False
    url = f'{prefix.rstrip("/")}/{destination.name}' if prefix else ''
    identifier = f'{artifact["region_id"]}:zones-company-continuous'
    original = deepcopy(artifact['multi_zone_layout'])
    for item in [*artifact.get('inputs', []), *artifact.get('reads', [])]:
        if item.get('channel') not in {'ring', 'layout_lower'}:
            continue
        item.update(original_channel=item['channel'], channel='split_arc_diagnostic',
                    matching=False, superseded_by=identifier)
        if 'text' in item:
            item['zone_label'] = '原分段读数（仅供核对）'
    artifact.setdefault('inputs', []).append({
        'id': identifier, 'name': 'zones-company-continuous', 'zone_name': 'company',
        'label': '连续公司环字', 'channel': 'ring', 'image_url': url,
        'ocr_input': True, 'status': 'completed',
    })
    row = observations[0]
    artifact['reads'].append({
        'id': identifier+':0', 'input_id': identifier, 'region_id': artifact['region_id'],
        'channel': 'ring', 'provider': provider, 'text': row.text,
        'confidence': row.confidence, 'image_url': url,
    })
    artifact['continuous_company'] = dict(geometry, status='adopted', original_layout=original)
    artifact['multi_zone_layout'].update(
        method='ellipse_continuous_name', status='prepared',
        arcs={'company': {key: geometry[key] for key in ('degrees', 'radial_range')}},
    )
    artifact.update(unwrapped_url=url, unwrapped_text=row.text, unwrap_texts=[row.text],
                    ring_border_detection=geometry['border'],
                    ring_input_operation='分别识别章型与编号，仅填白内部笔画，沿连续外环完整展开公司名称')
    artifact.pop('lower_arc_text', None)
    return True
