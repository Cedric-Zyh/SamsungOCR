"""Separate a visible double-border oval's company arc from its website.

Geometry and independent OCR determine the split; required wording is never
an input. Incomplete borders, non-URL lower words and uncertain reads retain
the existing ring path unchanged.
"""

from receipt_ocr.runtime.image_processing import image_operation
from pathlib import Path
import math
import re

import cv2
import numpy as np

from receipt_ocr.providers.paddle_runtime import variant_of


def _ellipse_points(ellipse, theta):
    (cx, cy), (a, b), angle = ellipse
    rotation = np.deg2rad(angle)
    return np.column_stack((
        cx+a/2*np.cos(theta)*np.cos(rotation)-b/2*np.sin(theta)*np.sin(rotation),
        cy+a/2*np.cos(theta)*np.sin(rotation)+b/2*np.sin(theta)*np.cos(rotation),
    ))


def _visible_edges(image, chroma):
    contours, _ = cv2.findContours((chroma > 45).astype('uint8'), cv2.RETR_LIST,
                                  cv2.CHAIN_APPROX_NONE)
    ellipses = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        if len(contour) < 20 or cv2.contourArea(contour) < image.shape[0]*image.shape[1]*.10:
            continue
        ellipse = cv2.fitEllipse(contour)
        center, axes, angle = ellipse
        if (min(axes) < min(image.shape[:2])*.28 or not .45 <= min(axes)/max(axes) <= .90
                or np.linalg.norm(np.asarray(center)-np.array(image.shape[1::-1])/2) > min(axes)*.07):
            continue
        points = contour[:, 0].astype(float)-center
        co, si = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
        rho = np.hypot((points[:, 0]*co+points[:, 1]*si)/(axes[0]/2),
                       (-points[:, 0]*si+points[:, 1]*co)/(axes[1]/2))
        # Only closed, well supported borders, never an ellipse fitted to text.
        if np.quantile(np.abs(rho-1), .95) <= .03:
            ellipses.append(ellipse)
    if len(ellipses) < 4:
        return None
    sizes = np.array([sum(e[1])/2 for e in ellipses])
    gaps = sizes[:-1]-sizes[1:]
    split = int(np.argmax(gaps))
    if (split < 1 or len(ellipses)-split-1 < 2
            or gaps[split] <= 2*np.median(np.abs(gaps))):
        return None
    outer, inner = ellipses[split], ellipses[split+1]
    if (np.linalg.norm(np.asarray(outer[0])-inner[0]) > min(outer[1])*.035
            or abs((outer[2]-inner[2]+90) % 180-90) > 5
            or not all(.40 < inside/outside < .88 for inside, outside in zip(inner[1], outer[1]))):
        return None
    return outer, inner


def double_border_pixels(image, *, color='red'):
    """Sample only the observed space between the inner and outer border ink."""
    blue, green, red = (channel.astype(float) for channel in cv2.split(image))
    chroma = np.clip(red-np.maximum(blue, green) if color == 'red'
                     else blue-np.maximum(red, green), 0, 255)
    edges = _visible_edges(image, chroma)
    if edges is None:
        return None
    outer, inner = edges
    theta = np.linspace(0, 2*np.pi, 3600, endpoint=False)
    outside, inside = _ellipse_points(outer, theta), _ellipse_points(inner, theta)
    fraction = np.linspace(.045, .955, 140)[:, None, None]
    mapping = outside[None, :, :]*(1-fraction)+inside[None, :, :]*fraction
    gray = np.rint(255-chroma).astype('uint8')
    strip = cv2.remap(gray, mapping[:, :, 0].astype('float32'), mapping[:, :, 1].astype('float32'),
                      cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    support = cv2.morphologyEx((np.mean(strip < 190, axis=0) > .04).astype('uint8')[None, :],
                              cv2.MORPH_CLOSE, np.ones((1, 51), np.uint8))[0]
    transitions = np.diff(np.r_[False, support == 0, False].astype(int))
    gaps = [(int(l), int(r)) for l, r in zip(np.where(transitions == 1)[0], np.where(transitions == -1)[0])
            if r-l >= 90]
    top = int(np.argmin(outside[:, 1]))
    before, after = [g for g in gaps if g[1] < top], [g for g in gaps if g[0] > top]
    if not before or not after:
        return None
    # Nearest character spaces can clip the first/last side glyph. Use the
    # widest independent separator on each side, with a two-degree margin.
    left = max(before, key=lambda gap: gap[1]-gap[0])
    right = max(after, key=lambda gap: gap[1]-gap[0])
    start, end = left[1]-20, right[0]+20
    if not 1200 <= end-start <= 3000:
        return None
    company = strip[:, start:end]
    with image_operation('rotation', '旋转校正'):
        website = cv2.rotate(np.concatenate([strip[:, right[1]-20:], strip[:, :left[0]+20]], axis=1),
                             cv2.ROTATE_180)
    overlay = image.copy()
    cv2.ellipse(overlay, outer, (80, 130, 80), 2)
    cv2.ellipse(overlay, inner, (130, 150, 160), 2)
    polygon = np.rint(np.concatenate([mapping[0, start:end], mapping[-1, start:end][::-1]])).astype('int32')
    mask = np.zeros(image.shape[:2], 'uint8')
    cv2.fillPoly(mask, [polygon], 255)
    preview = np.full_like(image, 255)
    preview[mask > 0] = image[mask > 0]
    def padded(pixels):
        pixels = cv2.copyMakeBorder(pixels, 12, 12, 20, 20, cv2.BORDER_CONSTANT, value=255)
        return cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)
    return {'company': padded(company), 'website': padded(website), 'overlay': overlay,
            'band': preview, 'full': padded(strip),
            'geometry': {'method': 'observed_double_border_blank_separation',
                         'outer_inner_edge': outer, 'inner_outer_edge': inner,
                         'company_degrees': [start/10, end/10], 'gaps': gaps}}


def _one_trusted(rows, minimum):
    rows = [row for row in rows if row.text]
    return (rows[0] if len(rows) == 1 and math.isfinite(float(rows[0].confidence))
            and float(rows[0].confidence) >= minimum else None)


def read_double_border(artifact, directory, index, prefix, *, reader, provider):
    """Commit two independently read fields only after both are unambiguous."""
    if (variant_of(provider) != 'v6' or artifact.get('shape_code') != 'ellipse'
            or artifact.get('multi_zone_layout') or artifact.get('double_border_layout')):
        return False
    centers = [row for row in artifact.get('reads', []) if row.get('channel') == 'center' and row.get('text')]
    if (len(centers) != 1 or not math.isfinite(float(centers[0].get('confidence') or 0))
            or float(centers[0].get('confidence') or 0) < .80):
        return False
    name = artifact.get('ring_input_source_name') or Path(artifact.get('ring_input_source_url') or '').name
    if not name:
        return False
    directory = Path(directory)
    image = cv2.imread(str(directory/Path(name).name))
    if image is None:
        return False
    pixels = double_border_pixels(image, color=artifact.get('color', 'red'))
    if pixels is None:
        return False
    paths = {}
    for key in ['company', 'website', 'overlay', 'band', 'full']:
        paths[key] = directory/f'seal-{index}-double-border-{key}.png'
        if not cv2.imwrite(str(paths[key]), pixels[key]):
            raise ValueError('双椭圆分区图片保存失败')
    company = _one_trusted(reader.read_line(paths['company'], provider=provider), .90)
    website = _one_trusted(reader.read_line(paths['website'], provider=provider), .80)
    if (company is None or website is None or not re.fullmatch(r'[\u4e00-\u9fff]{4,60}', company.text)
            or not re.fullmatch(r'(?:https?://)?(?:www\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,12}/?', website.text)):
        return False
    def url(key):
        return f'{prefix.rstrip("/")}/{paths[key].name}' if prefix else ''
    identifier = f'{artifact["region_id"]}:double-border-company'
    primary = {'text': artifact.get('unwrapped_text', ''), 'image_url': artifact.get('unwrapped_url', '')}
    for entry in [*artifact.get('inputs', []), *artifact.get('reads', [])]:
        if entry.get('channel') == 'ring':
            entry.update(original_channel='ring', channel='ring_diagnostic', matching=False,
                         superseded_by=identifier)
            if 'text' in entry:
                entry['zone_label'] = '原整环读数（仅供核对）'
    for key, channel, observation, label in [('company', 'ring', company, '环形公司名称'),
                                            ('website', 'website', website, '底部网址（单独保留）')]:
        uid = f'{artifact["region_id"]}:double-border-{key}'
        artifact['inputs'].append({'id': uid, 'name': f'double-border-{key}', 'label': label,
                                   'channel': channel, 'image_url': url(key), 'ocr_input': True,
                                   'status': 'completed', 'matching': key == 'company'})
        artifact['reads'].append({'id': uid+':0', 'input_id': uid, 'region_id': artifact['region_id'],
                                  'channel': channel, 'provider': provider, 'text': observation.text,
                                  'confidence': float(observation.confidence), 'image_url': url(key),
                                  'zone_label': label, 'matching': key == 'company'})
    artifact.update(
        double_border_layout=dict(pixels['geometry'], status='adopted', primary=primary,
                                  overlay_url=url('overlay'), website_url=url('website')),
        unwrapped_url=url('company'), unwrapped_text=company.text, unwrap_texts=[company.text],
        unwrapped_full_url=url('full'), website_text=website.text, website_url=url('website'),
        ring_text_band_url=url('band'), ring_border_url=url('overlay'),
        ring_geometry='observed_double_border_band',
        ring_input_operation='按实测内外椭圆边框定位文字带，依据两侧空白分离完整公司名称与底部网址，网址单独保留',
    )
    return True
