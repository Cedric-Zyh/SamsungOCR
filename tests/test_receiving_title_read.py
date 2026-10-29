"""A proven same-cell reread can correct bC, never invent requested letters."""
from copy import deepcopy

import pytest
from PIL import Image

from receipt_ocr.recognition.seal import receiving
from receipt_ocr.recognition.seal.postprocess.receiving_identity import decide_receiving_identity
from receipt_ocr.recognition.seal.postprocess.receiving_title_read import resolve_receiving_title_read

OLD = '北京永乐bC 010DCM'
TITLE = '北京永乐DC 010DCM'
PURPOSE = '收货专用章②'


def fixture(tmp_path):
    rows = [{'row_index': i, 'text': t, 'confidence': .98, 'source': 'receiving_primary', 'variants': []}
            for i, t in enumerate([OLD, PURPOSE, '收货人', '日期'])]
    rows[0]['variants'] = [
        {'text': TITLE, 'confidence': .979, 'source': 'same_physical_detection_crop'},
        {'text': OLD, 'confidence': .985, 'source': 'original_physical_row'}]
    detections = [{'row_index': 0, 'text': TITLE, 'confidence': .996, 'left': 0}]
    primary = decide_receiving_identity(rows, detected_boxes=detections, grid_verified=True)
    artifact = {'region_id': 'seal-0', 'index': 0, 'color': 'red', 'shape_code': 'rectangle',
                'original_url': '/files/test/original.png', 'color_isolated_url': '/files/test/color.png',
                'color_isolated_oriented_url': '/files/test/oriented.png',
                'rectangular_layout': 'frame_rows', 'inputs': [], 'reads': [], 'errors': [],
                'orientation': {'method': 'rectangle_rim', 'rotation_steps': [0.], 'applied_rotation': 0.},
                'rectangular_frame': {'direction': {'selected_rotation': 0}, 'measurement_width': 1000},
                'rectangular_fixed_rows': [
                    {'coordinate_space': 'oriented', 'crop_box': [0, i*100, 1000, i*100+90]}
                    for i in range(4)]}
    for name, size in [('original', (600, 400)), ('color', (1000, 667)), ('oriented', (1000, 667))]:
        Image.new('RGB', size, 'white').save(tmp_path / (name+'.png'))
    grid = {'accepted': True, 'source_size': [600, 400],
            'source_to_rotated_matrix': [[1000/600, 0, 0], [0, 667/400, 0]], 'rows': []}
    def append_input(channel, index, text, url, representation=None):
        name = channel + (representation or '')
        item = {'id': 'seal-0:'+name, 'channel': channel, 'rectangle_row_index': index,
                'image_url': url, 'read_mode': 'line', 'status': 'completed'}
        if representation:
            item.update(representation=representation, receiving_direction=0)
        artifact['inputs'].append(item)
        artifact['reads'].append({**item, 'id': item['id']+':0', 'input_id': item['id'],
                                  'text': text, 'confidence': .98, 'region_id': 'seal-0',
                                  'provider': 'paddle_v6'})
    append_input('receiving_detection', 0, TITLE, '/files/test/detection.png')
    grid_rows = []
    for i, text in enumerate([TITLE, '收货专用章', '收货人', '日期']):
        metadata = {'index': i, 'image_box': [0, i*100, 1000, i*100+90]}
        variants = []
        for source in ['color', 'gray']:
            url = f'/files/test/grid-{i}-{source}.png'
            metadata[source+'_url'] = url
            variants.append({'source': source, 'text': text, 'confidence': .98, 'image_url': url})
            if i == 0:
                append_input('receiving_grid', i, text, url, source)
        grid['rows'].append(metadata)
        grid_rows.append({'row_index': i, 'source_row_index': i, **variants[0], 'variants': variants})
    direction = {'direction': 0, 'rows': grid_rows, 'form_verified': True,
                 'decision': decide_receiving_identity(grid_rows, grid_verified=True)}
    grid['directions'] = [direction]
    return dict(artifact=artifact, primary=primary, rows=rows, detections=detections, grid=grid,
                directory=tmp_path, old_form=True, older_observations=[
                    {'rectangle_row_index': 0, 'text': OLD, 'confidence': .985}],
                selection={'text': '', 'can_compare': False, 'source': None, 'reason': 'primary_meaningful_conflict'})


def test_verified_rereads_correct_title_and_preserve_primary_circle(tmp_path):
    data = fixture(tmp_path)
    before = deepcopy(data)
    result = resolve_receiving_title_read(**data)
    assert result['selection']['text'] == TITLE + PURPOSE
    assert result['selection']['source'] == 'verified_title_reread'
    audit = result['audit']
    assert audit['policy'] == 'same_row_title_reread_consensus' and audit['accepted'] is True
    assert audit['before_texts'] == ['北京永乐bC010DCM']
    assert audit['geometry']['rows'][0]['grid_fraction'] == pytest.approx(1)
    assert result['resolved_primary']['conflicts'] == []
    assert data == before


@pytest.mark.parametrize('replacement', [
    '北京永乐BC 010DCM', '北京永乐DC 011DCM', '北京永乐bC 010bCM',
    '北京永兴bC 010DCM', '北京永乐bD 010DCM', '北京永乐bC 010DCM①'])
def test_other_company_code_number_or_letter_conflicts_remain(tmp_path, replacement):
    data = fixture(tmp_path)
    data['primary']['conflicts'][0]['observations'][0]['text'] = replacement
    assert resolve_receiving_title_read(**data) is None


@pytest.mark.parametrize('kind', [
    'no_grid', 'opposite_direction', 'multiple_directions', 'unverified_form', 'unverified_primary',
    'gray_disagrees', 'gray_low', 'gray_failed', 'gray_simulated', 'gray_wrong_url',
    'input_failed', 'input_other_region', 'read_other_region', 'read_other_provider',
    'read_low', 'read_other_row', 'grid_input_opposite_direction', 'missing_recrop',
    'detector_disagrees', 'detector_low', 'missing_detector', 'other_identity_conflict'])
def test_insufficient_or_unbound_corroboration_does_not_resolve(tmp_path, kind):
    data = fixture(tmp_path)
    a, p, g = data['artifact'], data['primary'], data['grid']
    direction = g['directions'][0]
    view = direction['rows'][0]['variants'][1]
    if kind == 'no_grid': g['accepted'] = False
    elif kind == 'opposite_direction': direction['direction'] = 180
    elif kind == 'multiple_directions': g['directions'].append(deepcopy(direction))
    elif kind == 'unverified_form': direction['form_verified'] = False
    elif kind == 'unverified_primary': data['old_form'] = False
    elif kind == 'gray_disagrees': view['text'] = OLD
    elif kind == 'gray_low': view['confidence'] = .89
    elif kind == 'gray_failed': view['status'] = 'failed'
    elif kind == 'gray_simulated': view['simulated'] = True
    elif kind == 'gray_wrong_url': view['image_url'] = '/files/other/grid.png'
    elif kind == 'input_failed': a['inputs'][0]['status'] = 'failed'
    elif kind == 'input_other_region': a['inputs'][0]['id'] = 'seal-1:receiving_detection'
    elif kind == 'read_other_region': a['reads'][0]['region_id'] = 'seal-1'
    elif kind == 'read_other_provider': a['reads'][0]['provider'] = 'qingtong'
    elif kind == 'read_low': a['reads'][0]['confidence'] = .89
    elif kind == 'read_other_row': a['reads'][0]['rectangle_row_index'] = 1
    elif kind == 'grid_input_opposite_direction': a['inputs'][1]['receiving_direction'] = 180
    elif kind == 'missing_recrop': p['conflicts'][0]['observations'] = [v for v in p['conflicts'][0]['observations'] if v['source'] != 'same_physical_detection_crop']
    elif kind == 'detector_disagrees': p['conflicts'][0]['observations'][-1]['text'] = OLD
    elif kind == 'detector_low': p['conflicts'][0]['observations'][-1]['confidence'] = .89
    elif kind == 'missing_detector': p['conflicts'][0]['observations'].pop()
    elif kind == 'other_identity_conflict': p['conflicts'].append({'row_index': 1, 'reason': 'disagreeing_whole_row_observations'})
    assert resolve_receiving_title_read(**data) is None


@pytest.mark.parametrize('kind', ['shifted_grid', 'swapped_rows', 'wrong_size', 'wrong_angle',
                                'multi_rotation', 'coarse_rotation', 'wrong_space', 'singular', 'nan', 'missing_image'])
def test_geometry_must_map_to_the_same_unique_measured_cells(tmp_path, kind):
    data = fixture(tmp_path)
    a, g = data['artifact'], data['grid']
    if kind == 'shifted_grid': g['source_to_rotated_matrix'][1][2] = 110
    elif kind == 'swapped_rows': g['rows'][0]['image_box'], g['rows'][1]['image_box'] = g['rows'][1]['image_box'], g['rows'][0]['image_box']
    elif kind == 'wrong_size': g['source_size'] = [601, 400]
    elif kind == 'wrong_angle': a['orientation']['applied_rotation'] = 1
    elif kind == 'multi_rotation': a['orientation']['rotation_steps'].append(180)
    elif kind == 'coarse_rotation': a['rectangular_frame']['direction']['selected_rotation'] = 180
    elif kind == 'wrong_space': a['rectangular_fixed_rows'][0]['coordinate_space'] = 'original'
    elif kind == 'singular': g['source_to_rotated_matrix'][0] = [0, 0, 0]
    elif kind == 'nan': g['source_to_rotated_matrix'][0][0] = float('nan')
    elif kind == 'missing_image': (tmp_path / 'oriented.png').unlink()
    assert resolve_receiving_title_read(**data) is None


def test_a_real_different_purpose_number_still_blocks(tmp_path):
    data = fixture(tmp_path)
    direction = data['grid']['directions'][0]
    direction['decision']['identity_rows'][1]['text'] = '收货专用章③'
    assert resolve_receiving_title_read(**data) is None


def test_unmapped_older_title_conflict_is_not_borrowed_as_same_row(tmp_path):
    data = fixture(tmp_path)
    data['older_observations'][0].pop('rectangle_row_index')
    assert resolve_receiving_title_read(**data) is None


def test_live_form_hook_adopts_the_corroborated_title_without_raw_rewrites(monkeypatch, tmp_path):
    data = fixture(tmp_path)
    artifact = data['artifact']
    # A real preexisting body purpose activates the form route before grid OCR.
    artifact['reads'].append({'text': PURPOSE, 'confidence': .94, 'channel': 'body', 'rectangle_row_index': 1})
    original = deepcopy(artifact['reads'])
    monkeypatch.setattr(receiving, 'read_primary', lambda *args: (deepcopy(data['rows']), deepcopy(data['detections'])))
    monkeypatch.setattr(receiving, 'prepare_receiving_grid', lambda *args: deepcopy(data['grid']))
    monkeypatch.setattr(receiving, '_grid_directions', lambda *args: deepcopy(data['grid']['directions']))
    monkeypatch.setattr(receiving, '_read_split', lambda *args: {'accepted': True, 'identity_text': TITLE+'收货专用章'})
    result = receiving.recognize_receiving_form(artifact, tmp_path, '/files/test', 'paddle_v6', object(), orientation_mode='combined')
    assert result == [TITLE+PURPOSE]
    form = artifact['receiving_form']
    assert form['status'] == 'adopted' and form['candidate_text'] == TITLE+PURPOSE
    assert form['primary']['conflicts'][0]['observations'][0]['text'] == OLD
    assert form['title_read_resolution']['accepted'] is True
    assert form['title_read_resolution']['resolved_primary']['conflicts'] == []
    assert [{k:v for k,v in read.items() if k != 'matching'} for read in artifact['reads']] == original
