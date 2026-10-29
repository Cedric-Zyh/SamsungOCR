"""Keep a real same-row receiving purpose and its consistently observed number."""
from copy import deepcopy

import pytest

from receipt_ocr.recognition.seal.postprocess.receiving_identity import decide_receiving_identity
from receipt_ocr.recognition.seal.postprocess.receiving_purpose import resolved_purpose_covers
from receipt_ocr.recognition.seal.ocr.receiving_number import read_receiving_number, _number_conflict


TITLE = '北京永乐DC010DCM'


def rows(*purpose_values):
    values = [{'text': text, 'confidence': .94 - i*.02, 'source': f'observed-{i}'}
              for i, text in enumerate(purpose_values or ('收费专用章②', '收费专用章', '收货专用章'))]
    return [{'row_index': 0, 'text': TITLE, 'confidence': .99},
            {'row_index': 1, **values[0], 'variants': values[1:]},
            {'row_index': 2, 'text': '收货人', 'confidence': .99},
            {'row_index': 3, 'text': '日期', 'confidence': .99}]


def decision(*values):
    return decide_receiving_identity(rows(*values), grid_verified=True)


def test_same_physical_purpose_combines_actual_receiving_word_and_unique_observed_circle():
    physical = rows()
    before = deepcopy(physical)
    result = decide_receiving_identity(physical, grid_verified=True)
    assert result['can_compare'] and not result['conflicts']
    assert result['identity_text'] == TITLE + '收货专用章②'
    resolution = result['purpose_resolution']
    assert resolution == result['identity_rows'][1]['purpose_resolution']
    assert {k: resolution[k] for k in ('policy', 'accepted', 'row_index', 'purpose_text', 'number_text', 'text')} == {
        'policy': 'observed_receiving_purpose', 'accepted': True, 'row_index': 1,
        'purpose_text': '收货专用章', 'number_text': '②', 'text': '收货专用章②'}
    assert [v['text'] for v in resolution['observations']] == ['收费专用章②', '收费专用章', '收货专用章']
    assert physical == before


@pytest.mark.parametrize('number', ['②', '(2)', '（２）', ''])
def test_number_is_taken_literally_and_missing_number_is_not_a_conflict(number):
    result = decision('收费专用章' + number, '收货专用章')
    assert result['identity_text'] == TITLE + '收货专用章' + number
    assert result['purpose_resolution']['number_text'] == number


@pytest.mark.parametrize('other', [
    '收货专用章③', '收货专用章(2)', '收货专用章2', '收贷专用章②',
    '☑收货专用章②', '某公司收货专用章②', '收货专用章(2）', '收货专用章②日期2',
])
def test_other_number_glyphs_purpose_words_and_extra_ink_are_not_discarded(other):
    result = decision('收费专用章②', '收货专用章', other)
    assert not result['can_compare']
    assert 'purpose_resolution' not in result


def test_fee_reading_alone_cannot_supply_an_unobserved_receiving_word():
    result = decision('收费专用章②', '收费专用章')
    assert result['identity_text'] == '' and not result['can_compare']
    assert 'no_observed_purpose' in result['warnings']


@pytest.mark.parametrize('unsupported', [
    {'confidence': .74}, {'confidence': 0}, {'confidence': True}, {'confidence': float('nan')},
    {'simulated': True}, {'status': '识别失败'}, {'status': 'failed'}, {'error': 'OCR failed'},
])
def test_low_confidence_failed_or_simulated_receiving_reading_cannot_authorize_resolution(unsupported):
    physical = rows('收费专用章②', '收货专用章')
    physical[1]['variants'][0].update(unsupported)
    result = decide_receiving_identity(physical, grid_verified=True)
    assert not result['can_compare'] and 'purpose_resolution' not in result


@pytest.mark.parametrize('unsupported', [{'simulated': True}, {'error': 'failed'}, {'status': '识别失败'}])
def test_failed_number_reading_is_not_used_to_complete_a_real_bare_purpose(unsupported):
    physical = rows('收费专用章②', '收货专用章')
    physical[1].update(unsupported)
    result = decide_receiving_identity(physical, grid_verified=True)
    assert result['identity_text'] == TITLE + '收货专用章'
    assert 'purpose_resolution' not in result


def test_measured_grid_is_required_before_the_purpose_can_be_resolved():
    result = decide_receiving_identity(rows(), grid_verified=False)
    assert not result['can_compare'] and 'purpose_resolution' not in result


def test_resolving_purpose_does_not_resolve_a_different_company_or_code():
    physical = rows()
    physical[0]['variants'] = [{'text': TITLE.replace('010', '011'), 'confidence': .99}]
    result = decide_receiving_identity(physical, grid_verified=True)
    assert not result['can_compare']
    assert result['purpose_resolution']['accepted'] is True
    assert result['conflicts'][0]['row_index'] == 0


def test_guard_exception_requires_this_row_this_resolution_and_an_actual_resolved_reading():
    result = decision()
    observation = {'text': '收费专用章②', 'confidence': .94, 'row_index': 1}
    assert resolved_purpose_covers(observation, result, result['identity_text'])
    for changed in ({'row_index': 0}, {'row_index': None}, {'text': '收费专用章③'},
                    {'text': '收费专用章2'}, {'simulated': True}, {'confidence': .5}):
        assert not resolved_purpose_covers({**observation, **changed}, result, result['identity_text'])
    assert not resolved_purpose_covers(observation, {}, result['identity_text'])
    assert not resolved_purpose_covers(observation, result, TITLE + '收货专用章③')


def test_already_resolved_number_does_not_trigger_redundant_number_ocr(tmp_path):
    result = decision()
    artifact = {'color': 'red', 'shape_code': 'rectangle', 'rectangular_layout': 'frame_rows',
                'matching_texts': [result['identity_text']],
                'reads': [{'channel': 'body', 'rectangle_row_index': 1, 'text': '收货专用章', 'confidence': .99}],
                'receiving_form': {'managed': True, 'status': 'adopted', 'identity_text': result['identity_text'],
                                   'primary': result}}
    before = deepcopy(artifact)
    assert read_receiving_number(artifact, tmp_path, '/files/test', 'paddle_v6', object()) is None
    assert artifact == before


def test_later_circle_reread_can_preserve_resolved_purpose_but_not_other_numbers():
    result = decision('收费专用章', '收货专用章')
    artifact = {'reads': [{'channel': 'body', 'rectangle_row_index': 1,
                          'text': '收费专用章', 'confidence': .94}],
                'receiving_form': {'managed': True, 'status': 'adopted', 'identity_text': result['identity_text'],
                                   'primary': result}}
    assert not _number_conflict(artifact, 1, '②')
    artifact['reads'][0]['text'] = '收费专用章③'
    assert _number_conflict(artifact, 1, '②')


def test_purpose_resolution_survives_the_later_zero_o_guard_on_the_same_proven_form(monkeypatch, tmp_path):
    from receipt_ocr.recognition.seal import receiving
    from test_receiving_recovery import SUBJECT, install_read_boundaries, stamp

    physical = rows()
    physical[0]['text'] = SUBJECT
    physical[0]['variants'] = [{'text': SUBJECT.replace('DC0', 'DCO'), 'confidence': .99}]
    boxes = [{'row_index': 1, 'text': '收货专用章', 'confidence': .99}]
    artifact = stamp(subject=SUBJECT)
    artifact['reads'] = [dict(row, channel='body', rectangle_row_index=row['row_index']) for row in physical]
    artifact['rectangular_frame'] = {'detected_boxes': deepcopy(boxes)}
    install_read_boundaries(monkeypatch, primary=physical, detections=boxes)
    expected = SUBJECT + '收货专用章②'
    assert receiving.recognize_receiving_form(artifact, tmp_path, '/files/test', 'paddle_v6', object(),
        orientation_mode='combined', requirement=expected) == [expected]
    form = artifact['receiving_form']
    assert form['requirement_code_resolution']['accepted'] is True
    assert form['primary']['purpose_resolution']['accepted'] is True
    assert form['primary']['conflicts'][0]['row_index'] == 0


def test_older_frame_rows_are_measured_without_reusing_orientation_coordinates():
    from receipt_ocr.recognition.seal.postprocess.receiving_policy import older_identity_observations
    from test_receiving_edge_noise import artifact, box, PURPOSE

    current = artifact(box('II', [100, 0, 109, 10]))
    current['orientation'] = {'detected_boxes': [
        box('方向图中的仓库名', [60, 30, 880, 110]), box(PURPOSE, [80, 160, 800, 260])
    ]}
    before = deepcopy(current)
    old = older_identity_observations(current)
    assert [entry['row_index'] for entry in old[:2]] == [0, 1]
    assert all('row_index' not in entry for entry in old[2:])
    assert current == before
