"""An older trailing outlier cannot erase three later agreeing physical reads."""
from copy import deepcopy

import pytest

from receipt_ocr.recognition.seal import receiving
from receipt_ocr.recognition.seal.postprocess.receiving_identity import decide_receiving_identity
from receipt_ocr.recognition.seal.postprocess.receiving_row_consensus import resolve_original_row_tail
from test_receiving_recovery import install_read_boundaries, physical_rows, stamp

TITLE = '北京永乐DC 010DCM'
PURPOSE = '收货专用章'


def readings():
    return [
        {'text': TITLE, 'confidence': .966, 'source': 'receiving_primary'},
        {'text': TITLE, 'confidence': .978, 'source': 'same_physical_detection_crop'},
        {'text': TITLE+'1', 'confidence': .920, 'source': 'original_physical_row'},
        {'text': TITLE, 'confidence': .991, 'source': 'detector_same_physical_row'},
    ]


def decide(values=None, purpose=PURPOSE, *, grid_verified=True):
    values = readings() if values is None else values
    primary = next(v for v in values if v['source'] == 'receiving_primary')
    variants = [deepcopy(v) for v in values if v['source'] not in {'receiving_primary', 'detector_same_physical_row'}]
    rows = [dict(primary, row_index=0, variants=variants),
            {'row_index': 1, 'text': purpose, 'confidence': .999},
            {'row_index': 2, 'text': '收货人', 'confidence': .99},
            {'row_index': 3, 'text': '日期', 'confidence': .99}]
    boxes = [dict(v, row_index=0) for v in values if v['source'] == 'detector_same_physical_row']
    return decide_receiving_identity(rows, detected_boxes=boxes, grid_verified=grid_verified)


def test_three_agreeing_rereads_keep_the_existing_complete_title_and_raw_outlier():
    values = readings()
    before = deepcopy(values)
    resolution = resolve_original_row_tail(values, 0, purpose_row_index=1)
    assert resolution['text'] == TITLE
    assert resolution['before_texts'] == [TITLE+'1']
    assert resolution['policy'] == 'same_row_reread_tail_consensus'
    assert resolution['observations'] == values
    assert resolution['minimum_supporting_confidence'] == .966
    result = decide(values)
    assert result['identity_text'] == TITLE+PURPOSE and result['can_compare'] is True
    assert result['conflicts'] == []
    assert result['row_resolutions'][0] == result['identity_rows'][0]['reread_resolution']
    assert any(v['text'] == TITLE+'1' for v in result['identity_rows'][0]['observations'])
    assert values == before


@pytest.mark.parametrize('suffix', ['2', '0', '①', '②', '（1）', '(1)', 'I', 'l', '丨', '11', 'A', '1A'])
def test_other_digits_real_number_glyphs_and_additional_characters_remain_conflicts(suffix):
    values = readings()
    values[2]['text'] = TITLE+suffix
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None
    assert decide(values)['can_compare'] is False


@pytest.mark.parametrize('text', [
    TITLE.replace('010', '011'), TITLE.replace('乐', '东'), TITLE.replace('DC ', 'bC '),
    TITLE.replace('010DCM', '010DCM1A'), '北京永乐DC 1010DCM',
])
def test_company_letter_and_internal_code_conflicts_are_not_corrections(text):
    values = readings()
    values[2]['text'] = text
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None
    assert decide(values)['can_compare'] is False


@pytest.mark.parametrize('which', [0, 1, 3])
@pytest.mark.parametrize('invalid', [.899, None, True, float('nan'), float('inf')])
def test_each_supporting_read_must_be_real_high_confidence(which, invalid):
    values = readings()
    values[which]['confidence'] = invalid
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None


@pytest.mark.parametrize('confidence', [.966, .967, .99, None, True, float('nan')])
def test_original_read_must_have_strictly_lower_confidence(confidence):
    values = readings()
    values[2]['confidence'] = confidence
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None


@pytest.mark.parametrize('source', ['receiving_primary', 'same_physical_detection_crop', 'detector_same_physical_row'])
def test_no_missing_or_disagreeing_supporting_source_is_allowed(source):
    values = readings()
    assert resolve_original_row_tail([v for v in values if v['source'] != source], 0, purpose_row_index=1) is None
    next(v for v in values if v['source'] == source)['text'] = TITLE+'1'
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None


@pytest.mark.parametrize('field,value', [('status', 'failed'), ('simulated', True), ('error', 'failed')])
def test_failed_or_simulated_read_cannot_vote(field, value):
    values = readings()
    values[1][field] = value
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None


def test_other_sources_or_duplicate_sources_cannot_supply_the_three_votes():
    values = readings()
    values[1]['source'] = 'gray'
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None
    values[1]['source'] = 'receiving_primary'
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None
    assert resolve_original_row_tail(readings()+[readings()[0]], 0, purpose_row_index=1) is None


@pytest.mark.parametrize('row,purpose', [(1, 1), (2, 1), (True, 1), (0, True), (0, None), (-1, 1)])
def test_purpose_numbers_and_unbound_row_indices_cannot_use_the_exception(row, purpose):
    assert resolve_original_row_tail(readings(), row, purpose_row_index=purpose) is None


@pytest.mark.parametrize('title', ['010DCM', '北京永乐DC0101', '收货专用章', '收货专用章②', '北京永乐DC010DCM②'])
def test_only_a_complete_letter_ending_title_can_be_retained(title):
    values = readings()
    for value in values:
        value['text'] = title + ('1' if value['source'] == 'original_physical_row' else '')
    assert resolve_original_row_tail(values, 0, purpose_row_index=1) is None


def test_existing_circle_is_retained_and_no_missing_circle_is_invented():
    assert decide(purpose=PURPOSE+'②')['identity_text'] == TITLE+PURPOSE+'②'
    assert decide()['identity_text'] == TITLE+PURPOSE
    assert decide(grid_verified=False)['can_compare'] is False
    values = readings()
    rows = [dict(values[0], row_index=0, variants=values[1:3]),
            {'row_index': 1, 'text': PURPOSE+'②', 'confidence': .99,
             'variants': [{'text': PURPOSE+'③', 'confidence': .95}]},
            {'row_index': 2, 'text': '收货人', 'confidence': .99}]
    result = decide_receiving_identity(rows, detected_boxes=[dict(values[3], row_index=0)], grid_verified=True)
    assert result['can_compare'] is False
    assert any(c['row_index'] == 1 for c in result['conflicts'])


def test_live_hook_keeps_title_without_accepting_disagreeing_grid_or_split(monkeypatch, tmp_path):
    values = readings()
    rows = physical_rows([TITLE, PURPOSE, '收货人', '日期'])
    rows[0] = dict(values[0], row_index=0, variants=deepcopy(values[1:3]))
    boxes = [dict(values[3], row_index=0)]
    grid_rows = physical_rows([TITLE, PURPOSE, '收货人', '日期'])
    grid_rows[0]['variants'][1]['text'] = TITLE.replace('乐', '东')
    artifact = stamp(subject=TITLE)
    artifact['reads'] = [dict(values[2], channel='body', rectangle_row_index=0),
                         {'channel': 'body', 'rectangle_row_index': 1, 'text': PURPOSE, 'confidence': .99}]
    before = deepcopy(artifact['reads'])
    install_read_boundaries(monkeypatch, primary=rows, detections=boxes,
                            directions=[{'direction': 0, 'rows': grid_rows}], accepted=True)
    monkeypatch.setattr(receiving, '_read_split', lambda *args: {
        'accepted': False, 'reason': 'literal_segment_disagreement', 'requires_review': True})
    assert receiving.recognize_receiving_form(
        artifact, tmp_path, '/files/test', 'paddle_v6', object(), orientation_mode='combined') == [TITLE+PURPOSE]
    form = artifact['receiving_form']
    assert form['status'] == 'adopted' and form['selected_source'] == 'primary'
    assert form['candidate_text'] == TITLE+PURPOSE
    assert form['primary']['row_resolutions'][0]['before_texts'] == [TITLE+'1']
    assert form['grid']['directions'][0]['decision']['conflicts']
    assert form['split']['accepted'] is False
    assert [{k:v for k,v in read.items() if k != 'matching'} for read in artifact['reads']] == before
