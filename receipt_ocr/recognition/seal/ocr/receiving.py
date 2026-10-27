"""Read and account for receiving-form inputs without a requested value."""
from pathlib import Path
import math

import numpy as np
from PIL import Image, ImageOps

from ..postprocess.receiving_identity import map_receiving_detections, verify_blank_row


def local_image(directory, url):
    return Path(directory) / Path(str(url).split('?', 1)[0]).name


def read_input(artifact, reader, provider, path, item):
    artifact['inputs'].append(item)
    try:
        observations = reader.read_line(path, provider=provider)
        item['status'] = 'completed' if observations else 'empty'
        for position, row in enumerate(observations):
            artifact['reads'].append({
                'id': f"{item['id']}:{position}", 'input_id': item['id'],
                'region_id': artifact['region_id'], 'channel': item['channel'],
                'provider': provider, 'text': row.text, 'confidence': row.confidence,
                'image_url': item['image_url'], 'matching': False,
                'zone_label': item['label'],
                **{key: item[key] for key in ['representation', 'split_part', 'split_scale', 'rectangle_row_index'] if key in item},
            })
        return {'text': ''.join(row.text for row in observations),
                'confidence': min((row.confidence for row in observations), default=0),
                'source': item.get('representation', item['channel']), 'image_url': item['image_url']}
    except Exception as exc:
        item.update(status='failed', error=str(exc))
        artifact['errors'].append({'step': item['channel'], 'message': str(exc)})
        return {'text': '', 'confidence': 0, 'source': item['channel'], 'image_url': item['image_url']}


def input_item(artifact, name, url, label, channel, **metadata):
    return {'id': f"{artifact['region_id']}:{name}", 'name': name, 'label': label,
            'channel': channel, 'image_url': url, 'ocr_input': True,
            'read_mode': 'line', 'matching': False, **metadata}


def read_primary(artifact, original, directory, prefix, provider, reader):
    """Reread whole cells with known 8px margins, retaining detector evidence."""
    prepared = original.get('rectangular_fixed_rows') or []
    urls = original.get('rectangular_row_urls') or []
    if original.get('rectangular_layout') not in {'frame_rows', 'single_detection'}:
        return [], []
    heights = [row['crop_box'][3]-row['crop_box'][1] for row in prepared
               if len(row.get('crop_box') or []) == 4]
    reference = float(np.median(heights)) if heights else None
    rows = []
    for index, url in enumerate(urls):
        name = f'receiving-primary-row-{index}'
        path = Path(directory) / f"seal-{artifact['index']}-{name}.png"
        source = (local_image(directory, url) if url else
                  Path(prepared[index].get('path') or Path(directory) / f"seal-{artifact['index']}-rectangular-row-{index}.png"))
        with Image.open(source) as image:
            image = image.convert('RGB')
            if min(image.size) <= 40:
                raise ValueError('接收章文字行缺少可核对的原始边距')
            ImageOps.expand(image.crop((20, 20, image.width-20, image.height-20)),
                            border=8, fill='white').save(path)
        item = input_item(artifact, name, f"{prefix}/{path.name}" if prefix else '',
                          f'表格整行补读 {index+1}', 'receiving_primary', rectangle_row_index=index)
        row = dict(read_input(artifact, reader, provider, path, item), row_index=index, variants=[])
        if not row['text']:
            row['blank_check'] = verify_blank_row(path, known_padding=8, reference_row_height=reference)
            row['is_blank'] = row['blank_check']['blank']
        rows.append(row)
    mapped = map_receiving_detections(original)
    canonical = next((i.get('image_url') for i in original.get('inputs') or []
                      if i.get('name') == 'rectangle-layout'), None)
    by_row = {}
    canonical_path = (local_image(directory, canonical) if canonical else
                      Path(directory) / f"seal-{artifact['index']}-rectangle-layout.png")
    if mapped and canonical_path.is_file():
        with Image.open(canonical_path) as image:
            image = image.convert('RGB')
            for box in mapped:
                points = box['points']
                bounds = [max(0, math.floor(min(p[0] for p in points))),
                          max(0, math.floor(min(p[1] for p in points))),
                          min(image.width, math.ceil(max(p[0] for p in points))),
                          min(image.height, math.ceil(max(p[1] for p in points)))]
                if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                    continue
                name = f"receiving-detection-{box['detection_index']}"
                path = Path(directory) / f"seal-{artifact['index']}-{name}.png"
                ImageOps.expand(image.crop(bounds), border=8, fill='white').save(path)
                item = input_item(artifact, name, f"{prefix}/{path.name}" if prefix else '',
                                  '同一物理行的文字框复读', 'receiving_detection',
                                  rectangle_row_index=box['row_index'])
                value = read_input(artifact, reader, provider, path, item)
                by_row.setdefault(box['row_index'], []).append((bounds[0], value))
    for row in rows:
        values = [value for _, value in sorted(by_row.get(row['row_index'], []), key=lambda pair: pair[0]) if value['text']]
        if values:
            row['variants'].append({'text': ''.join(v['text'] for v in values),
                                    'confidence': min(v['confidence'] for v in values),
                                    'source': 'same_physical_detection_crop'})
    return rows, mapped


def read_grid(artifact, grid, directory, prefix, provider, reader):
    directions = []
    for angle in [0, 180]:
        cells = grid['rows'] if not angle else list(reversed(grid['rows']))
        rows = []
        for index, cell in enumerate(cells):
            row = {'row_index': index, 'variants': [], 'box': cell['box'],
                   'box_coordinate_space': 'unrotated_grid_whole',
                   'source_row_index': cell['index']}
            for kind in ['color', 'gray']:
                path, url = Path(cell[f'{kind}_path']), cell[f'{kind}_url']
                name = f'receiving-grid-{angle}-row-{index}-{kind}'
                if angle:
                    path = Path(directory) / f"seal-{artifact['index']}-{name}.png"
                    with Image.open(cell[f'{kind}_path']) as image:
                        image.transpose(Image.Transpose.ROTATE_180).save(path)
                    url = f'{prefix}/{path.name}' if prefix else ''
                item = input_item(artifact, name, url, f'完整格线 {angle}° · 第{index+1}行 · {kind}',
                                  'receiving_grid', rectangle_row_index=index, representation=kind,
                                  receiving_direction=angle)
                row['variants'].append(read_input(artifact, reader, provider, path, item))
            row.update(**row['variants'][0])
            rows.append(row)
        directions.append({'direction': angle, 'rows': rows})
    return directions
