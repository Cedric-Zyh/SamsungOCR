"""Re-read a measured terminal cloud without editing observed characters."""
import math
from pathlib import Path

import cv2

from ..preprocess.company_noise import propose_company_noise_trim


DIAGNOSTIC_CHANNEL = 'company_noise_diagnostic'


def _confidence(value):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def read_company_noise(artifact, source, directory, index, prefix, *, reader, provider):
    """Keep the active ring unless three complete, high-score reads agree.

    Geometry accepts no requested text. All attempted inputs and observations
    stay diagnostic until agreement; purpose, number and lower rows are untouched.
    Real prediction accounting remains at the existing OcrReader boundary.
    """
    image = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return False
    proposal = propose_company_noise_trim(image)
    evidence = {
        'method': 'terminal_cloud_three_reads_v1',
        'status': 'not_applicable', 'reason': proposal['measurements']['reason'],
        'source_size': [image.shape[1], image.shape[0]],
        'bounds': proposal['bounds'], 'measurements': proposal['measurements'],
    }
    artifact['company_noise'] = evidence
    if not proposal['accepted']:
        return False
    active = [item for item in artifact.get('inputs', [])
              if item.get('channel') == 'ring' and not item.get('superseded_by')]
    if len(active) != 1:
        evidence.update(status='rejected', reason='ambiguous_active_ring')
        return False
    baseline = active[0]
    baseline_reads = [row for row in artifact.get('reads', [])
                      if row.get('channel') == 'ring' and row.get('input_id') == baseline['id']]
    if len(baseline_reads) != 1:
        evidence.update(status='rejected', reason='ambiguous_active_read')
        return False
    evidence.update(status='rejected', source_input_id=baseline['id'],
                    source_image_url=baseline['image_url'])
    directory = Path(directory)
    left, top, right, bottom = proposal['bounds']
    crop = image[top:bottom, left:right]
    cropped = directory / f'seal-{index}-company-noise-crop.png'
    padded = directory / f'seal-{index}-company-noise-padding.png'
    padding = cv2.copyMakeBorder(crop, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    for path, pixels in ((cropped, crop), (padded, padding)):
        if not cv2.imwrite(str(path), pixels):
            raise ValueError('环字杂纹校验图片保存失败')
    plans = [
        ('company-noise-detection', Path(source), '原连续环字检测校验（仅供核对）', reader.read),
        ('company-noise-crop', cropped, '环形公司名称（首尾杂纹隔离后）', reader.read_line),
        ('company-noise-padding', padded, '加白边重读校验（仅供核对）', reader.read_line),
    ]
    views = []
    for name, path, label, read in plans:
        identifier = f'{artifact["region_id"]}:{name}'
        url = f'{prefix.rstrip("/")}/{path.name}' if prefix else ''
        item = {'id': identifier, 'name': name, 'zone_name': 'company',
                'label': label, 'channel': DIAGNOSTIC_CHANNEL, 'image_url': url,
                'ocr_input': True, 'superseded_by': baseline['id']}
        artifact.setdefault('inputs', []).append(item)
        try:
            observations = list(read(path, provider=provider))
        except Exception as exc:
            message = f'环字杂纹校验失败（{type(exc).__name__}）'
            item.update(status='failed', error=message)
            artifact.setdefault('errors', []).append({'step': 'company_noise', 'message': message})
            evidence.update(reason='ocr_failed', failed_input_id=identifier)
            return False
        item['status'] = 'completed' if observations else 'empty'
        rows = []
        for position, observation in enumerate(observations):
            row = {'id': f'{identifier}:{position}', 'input_id': identifier,
                   'region_id': artifact['region_id'], 'channel': DIAGNOSTIC_CHANNEL,
                   'provider': provider, 'text': str(observation.text or '').strip(),
                   'confidence': _confidence(observation.confidence), 'image_url': url,
                   'matching': False, 'zone_label': label}
            artifact.setdefault('reads', []).append(row)
            rows.append(row)
        views.append((item, rows))
        if (len(rows) != 1 or not rows[0]['text']
                or rows[0]['confidence'] is None or not .95 <= rows[0]['confidence'] <= 1):
            evidence.update(reason='incomplete_or_low_confidence', failed_input_id=identifier)
            return False
    if len({rows[0]['text'] for _, rows in views}) != 1:
        evidence['reason'] = 'conflicting_readings'
        return False

    selected_input, selected_rows = views[1]
    selected = selected_rows[0]
    for item in [baseline, *[item for item, _ in views]]:
        item.update(channel=DIAGNOSTIC_CHANNEL, superseded_by=selected_input['id'])
    baseline_reads[0].update(channel=DIAGNOSTIC_CHANNEL, original_channel='ring', matching=False,
                             superseded_by=selected_input['id'],
                             zone_label='原连续环字读数（仅供核对）')
    selected_input.update(channel='ring')
    selected_input.pop('superseded_by', None)
    selected.update(channel='ring', zone_label='环形公司名称')
    artifact.update(unwrapped_url=selected['image_url'], unwrapped_text=selected['text'],
                    unwrap_texts=[selected['text']],
                    ring_input_operation='分别识别章型与编号；隔离有完整空隙的首尾杂纹，三路重读一致后采用公司环字')
    evidence.update(status='adopted', reason='three_complete_readings_agree',
                    selected_input_id=selected_input['id'], text=selected['text'],
                    confidence=min(rows[0]['confidence'] for _, rows in views),
                    input_ids=[item['id'] for item, _ in views])
    return True
