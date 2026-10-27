"""A code/telephone/company seal without a stamp-purpose orientation anchor."""
from pathlib import Path
import re

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus
from receipt_ocr.imaging.ring_border import trace_ellipse_border
from .company_border import company_border_points
from .orientation_geometry import rotate_stamp_image, _rotation_geometry
from .row_extent import save_row_polygon
from .station_layout import proportional_arc


def valid_code_phone_fields(code, phone, company):
    values = [re.sub(r'\s+', '', str(value or '')) for value in (code, phone, company)]
    return bool(re.fullmatch(r'站代码[:：]?\d{6,12}', values[0])
                and re.fullmatch(r'0\d{10}', values[1])
                and len(values[2]) >= 6
                and values[2].endswith(('有限公司', '有限责任公司', '服务中心', '服务部')))


def _one_read(reader, path, provider):
    rows = reader.read_line(path, provider=provider)
    if (len(rows) != 1 or not rows[0].text or not np.isfinite(rows[0].confidence)
            or rows[0].confidence < .95):
        return None
    return rows[0]


def prepare_code_phone_layout(source, directory, index, *, reader, provider, color='red'):
    """Try cardinal directions; only a verified complete layout selects one."""
    directory = Path(directory)
    attempts = []
    for coarse in (0, 180, 90, 270):
        candidate = Path(source)
        if coarse:
            directory.mkdir(parents=True, exist_ok=True)
            candidate = directory / f'seal-{index}-code-phone-coarse-{coarse}.png'
            rotate_stamp_image(source, candidate, coarse)
        plan = _prepare_code_phone_candidate(candidate, directory, index,
                                             reader=reader, provider=provider, color=color)
        attempts.append({'coarse_rotation': coarse, 'complete': plan is not None})
        if plan is None:
            continue
        fine = plan['orientation']['fine_rotation']
        plan['orientation'].update(
            model='完整电话号码四向定位与旋正复核',
            coarse_rotation=float(coarse), applied_rotation=float(coarse) + fine,
            rotation_steps=([float(coarse)] if coarse else []) + [fine],
            phone_direction_candidates=attempts,
            status=f'按 {coarse}° 粗校正，再按已验证电话微调 {fine:.1f}°',
        )
        return plan
    return None


def _prepare_code_phone_candidate(source, directory, index, *, reader, provider, color='red'):
    """Adopt only three independently complete readings from one physical seal.

    No expected stamp wording, code, phone or company is accepted here. A
    telephone is a direction anchor only after the same row reads identically
    on the rotated whole image; a station-code label and legal company must
    subsequently establish this particular layout.
    """
    image = cv2.imread(str(source))
    if image is None:
        return None
    height, width = image.shape[:2]
    phones = []
    for box in reader.detect_boxes(source, provider=provider):
        text = re.sub(r'\s+', '', str(box.get('text') or ''))
        score = float(box.get('confidence') or 0)
        points = np.asarray(box.get('points', []), float)
        if (not re.fullmatch(r'0\d{10}', text) or not np.isfinite(score) or score < .95
                or points.shape != (4, 2) or not np.isfinite(points).all()):
            continue
        middle = points.mean(0)
        if (.2 * width < middle[0] < .8 * width and .2 * height < middle[1] < .8 * height
                and points.min() >= 0 and points[:, 0].max() <= width
                and points[:, 1].max() <= height):
            phones.append((text, points))
    if len(phones) != 1:
        return None
    phone, points = phones[0]
    edge = points[1] - points[0]
    angle = float(np.rad2deg(np.arctan2(edge[1], edge[0])))
    if abs(angle) > 45:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    def path(name):
        return directory / f'seal-{index}-code-phone-{name}.png'

    matrix, _, _ = _rotation_geometry(width, height, angle)
    rotate_stamp_image(source, path('oriented'), angle)
    polygon = cv2.transform(points.astype('float32')[None], matrix.astype('float32'))[0]
    save_row_polygon(path('oriented'), path('telephone'), polygon, padding=12)
    telephone = _one_read(reader, path('telephone'), provider)
    if telephone is None or re.sub(r'\s+', '', telephone.text) != phone:
        return None
    # Mask the verified telephone's own strokes; keep the rim and both arcs.
    from .inputs import _white_detected_type_region
    _white_detected_type_region(path('oriented'), path('ring-input'),
                               polygons=[polygon.tolist()], mask_destination=path('mask'))
    intact = cv2.imread(str(path('oriented')))
    guide = trace_ellipse_border(path('oriented'), color=color)
    correction = None
    if guide is not None:
        _, correction = company_border_points(guide)
        ellipse = correction['ellipse'] if correction else guide.annulus[0][2]
    else:
        annulus = _fit_ellipse_annulus(_ocr_color_mask(intact, color))
        if annulus is None:
            return None
        ellipse = annulus[0][2]
    (cx, cy), (a, b), turn = ellipse
    if a < b:
        a, b, turn = b, a, turn - 90
    turn = (turn + 90) % 180 - 90
    geometry = (cx, cy, a / 2, b / 2, turn)
    masked = cv2.imread(str(path('ring-input')))
    blue, green, red = (channel.astype(float) for channel in cv2.split(masked))
    chroma = red - np.maximum(blue, green) if color == 'red' else blue - np.maximum(red, green)
    gray = (255 - np.clip(chroma * 255 / 160, 0, 255)).astype('uint8')
    reads = {'phone': telephone}
    arcs = {}
    for name, degrees, lower in [('code', [-200, 20], False), ('company', [160, 20], True)]:
        pixels = proportional_arc(gray, geometry, degrees, [.60, .94], lower=lower)
        if not cv2.imwrite(str(path(name)), pixels):
            raise ValueError('站代码印章分区图片保存失败')
        row = _one_read(reader, path(name), provider)
        if row is None:
            return None
        reads[name] = row
        arcs[name] = {'degrees': degrees, 'radial_range': [.60, .94]}
    if not valid_code_phone_fields(reads['code'].text, telephone.text, reads['company'].text):
        return None
    overlay = intact.copy()
    cv2.ellipse(overlay, ellipse, (80, 110, 80), 3)
    cv2.polylines(overlay, [np.rint(polygon).astype('int32')], True, (80, 110, 80), 3)
    if not cv2.imwrite(str(path('regions')), overlay):
        raise ValueError('站代码印章分区标记保存失败')
    return {
        'method': 'ellipse_code_phone_rows', 'reads': reads,
        'oriented': path('oriented'), 'overlay': path('regions'),
        'ring_input': path('ring-input'), 'mask': path('mask'), 'arcs': arcs,
        'paths': {'code': path('code'), 'phone': path('telephone'), 'company': path('company')},
        'border': {'method': 'smooth_code_phone_ellipse', 'ellipse': ellipse,
                   'company_arc_correction': correction},
        'orientation': {'mode': 'combined', 'model': '完整电话号码定位与旋正复核',
                        'anchor_text': phone, 'confidence': float(telephone.confidence),
                        'applied_rotation': angle, 'coarse_rotation': 0., 'fine_rotation': angle,
                        'rotation_steps': [angle], 'status': '按已验证电话号码旋正'},
    }
