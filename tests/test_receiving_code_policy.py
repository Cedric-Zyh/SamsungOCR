"""Requirement-directed O/0 fallback never rewrites raw receiving evidence."""
from copy import deepcopy

import pytest

from receipt_ocr.recognition.seal import receiving
from test_receiving_recovery import (
    IDENTITY, PURPOSE, SUBJECT, form_rows, install_read_boundaries, physical_rows, stamp,
)


def _run(artifact, tmp_path, requirement=IDENTITY):
    return receiving.recognize_receiving_form(
        artifact, tmp_path, '/files/test', 'paddle_v6', object(),
        orientation_mode='combined', requirement=requirement)


def _conflicting_rows():
    rows = form_rows()
    rows[0]['variants'][1]['text'] = SUBJECT.replace('DC0', 'DCO')
    return rows


def _raw_reads(artifact):
    return [{key: value for key, value in row.items() if key != 'matching'}
            for row in artifact['reads']]


def test_only_requirement_code_policy_resolves_one_physical_row_zero_o_conflict(monkeypatch, tmp_path):
    artifact = stamp()
    rows = _conflicting_rows()
    before_rows, before_reads = deepcopy(rows), deepcopy(_raw_reads(artifact))
    install_read_boundaries(monkeypatch, primary=rows)
    assert _run(artifact, tmp_path) == [IDENTITY]
    form = artifact['receiving_form']
    assert form['primary']['can_compare'] is False
    assert form['primary']['conflicts'][0]['reason'] == 'disagreeing_whole_row_observations'
    audit = form['requirement_code_resolution']
    assert audit['accepted'] is True
    assert audit['original_block']['reason'] == 'primary_meaningful_conflict'
    assert audit['adjustments'][0]['raw_text'] == rows[0]['variants'][1]['text']
    assert audit['adjustments'][0]['corrected_code'] == 'DC021DC0'
    assert audit['comparison_primary']['can_compare'] is True
    assert rows == before_rows and _raw_reads(artifact) == before_reads


def test_default_empty_requirement_preserves_the_existing_blocked_behavior(monkeypatch, tmp_path):
    artifact = stamp()
    install_read_boundaries(monkeypatch, primary=_conflicting_rows())
    assert _run(artifact, tmp_path, '') == []
    assert artifact['receiving_form']['reason'] == 'primary_meaningful_conflict'
    assert 'requirement_code_resolution' not in artifact['receiving_form']


def test_a_usable_original_identity_stays_raw_even_under_a_different_required_code(monkeypatch, tmp_path):
    observed = SUBJECT.replace('DC0', 'DCO')
    artifact = stamp(subject=observed)
    rows = physical_rows([observed, PURPOSE, '收货人', '日期'])
    install_read_boundaries(monkeypatch, primary=rows)
    assert _run(artifact, tmp_path) == [observed + PURPOSE]
    assert artifact['receiving_form']['selected_source'] == 'primary'
    assert 'requirement_code_resolution' not in artifact['receiving_form']


@pytest.mark.parametrize('kind', ['name', 'numeric_code', 'stamp_number', 'missing_name'])
def test_other_reliable_conflicts_still_block_after_zero_o_resolution(monkeypatch, tmp_path, kind):
    artifact = stamp()
    rows = _conflicting_rows()
    if kind == 'stamp_number':
        rows[1]['variants'][1]['text'] = '收货专用章②'
    else:
        text = {'name': SUBJECT.replace('朱泾', '另一'),
                'numeric_code': SUBJECT.replace('021', '031'),
                'missing_name': 'DC021DC0'}[kind]
        rows[0]['variants'].append({'source': 'independent', 'text': text, 'confidence': .99})
    install_read_boundaries(monkeypatch, primary=rows)
    assert _run(artifact, tmp_path) == []
    audit = artifact['receiving_form']['requirement_code_resolution']
    assert audit['accepted'] is False
    assert artifact['receiving_form']['primary']['conflicts']
    assert artifact['matching_texts'] == []


@pytest.mark.parametrize('requirement', [IDENTITY.replace('朱泾', '另一'),
                                       IDENTITY.replace('021', '031'), IDENTITY.replace('①', '②')])
def test_complete_identity_must_still_fit_the_requirement(monkeypatch, tmp_path, requirement):
    artifact = stamp()
    install_read_boundaries(monkeypatch, primary=_conflicting_rows())
    assert _run(artifact, tmp_path, requirement) == []
    assert artifact['receiving_form']['status'] == 'conflict'


def test_verified_grid_dual_view_code_disagreement_can_use_the_policy(monkeypatch, tmp_path):
    artifact = stamp(frame=False)
    rows = _conflicting_rows()
    before = deepcopy(rows)
    install_read_boundaries(monkeypatch, directions=[{'direction': 0, 'rows': rows}], accepted=True)
    assert _run(artifact, tmp_path) == [IDENTITY]
    form = artifact['receiving_form']
    assert form['selected_source'] == 'verified_grid_recovery'
    raw_direction = form['grid']['directions'][0]
    assert raw_direction['form_verified'] is True
    assert raw_direction['decision']['can_compare'] is False
    assert raw_direction['rows'][0]['variants'][1]['text'] == before[0]['variants'][1]['text']
    assert form['requirement_code_resolution']['comparison_grid_alternatives'][0]['dual_representation_verified']
    assert rows == before


@pytest.mark.parametrize('unproven', ['geometry', 'recipient', 'purpose', 'missing_gray', 'low_gray'])
def test_code_policy_cannot_prove_a_grid_or_supply_missing_observations(monkeypatch, tmp_path, unproven):
    artifact = stamp(frame=False)
    rows = _conflicting_rows()
    if unproven == 'recipient': rows[2]['text'] = '未知内容'
    if unproven == 'recipient':
        for value in rows[2]['variants']: value['text'] = '未知内容'
    if unproven == 'purpose':
        rows[1]['text'] = '收货专用早'
        for value in rows[1]['variants']: value['text'] = '收货专用早'
    if unproven == 'missing_gray': rows[0]['variants'] = rows[0]['variants'][:1]
    if unproven == 'low_gray': rows[0]['variants'][1]['confidence'] = .5
    install_read_boundaries(monkeypatch, directions=[{'direction': 0, 'rows': rows}],
                            accepted=unproven != 'geometry')
    assert _run(artifact, tmp_path) in (None, [])
    assert artifact.get('matching_texts', []) == []


def test_older_complete_title_may_resolve_but_its_raw_reading_is_preserved(monkeypatch, tmp_path):
    old_title = SUBJECT.replace('DC0', 'DCO')
    artifact = stamp(frame=False)
    artifact['reads'] = [dict(row, rectangle_row_index=row['row_index'], channel='body')
                         for row in physical_rows([old_title, PURPOSE, '收货人', '日期'])]
    before_reads = deepcopy(_raw_reads(artifact))
    install_read_boundaries(monkeypatch, directions=[{'direction': 0, 'rows': form_rows()}], accepted=True)
    assert _run(artifact, tmp_path) == [IDENTITY]
    audit = artifact['receiving_form']['requirement_code_resolution']
    assert audit['original_block']['reason'] == 'older_reliable_fragment_conflict'
    assert any(item['source'].startswith('older[') for item in audit['adjustments'])
    assert _raw_reads(artifact) == before_reads


def test_an_older_code_only_fragment_keeps_its_blocking_force(monkeypatch, tmp_path):
    artifact = stamp()
    artifact['reads'].append({'text': 'DCO', 'confidence': .99, 'channel': 'body', 'rectangle_row_index': 0})
    install_read_boundaries(monkeypatch, primary=_conflicting_rows())
    assert _run(artifact, tmp_path) == []
    audit = artifact['receiving_form']['requirement_code_resolution']
    assert audit['accepted'] is False
    assert audit['comparison_selection']['reason'] == 'remaining_reliable_identity_conflict'
    assert artifact['reads'][-1]['text'] == 'DCO'


def test_a_non_code_conflict_in_another_verified_view_still_blocks(monkeypatch, tmp_path):
    artifact = stamp()
    other = physical_rows([SUBJECT.replace('朱泾', '另一'), PURPOSE, '收货人', '日期'])
    install_read_boundaries(monkeypatch, primary=_conflicting_rows(),
                            directions=[{'direction': 0, 'rows': other}], accepted=True)
    assert _run(artifact, tmp_path) == []
    assert artifact['receiving_form']['requirement_code_resolution']['accepted'] is False


@pytest.mark.parametrize('row_index,fragment', [(1, '1'), (0, '021'), (0, '朱泾')])
def test_a_grid_whole_row_disagreement_is_not_just_a_compatible_substring(
        monkeypatch, tmp_path, row_index, fragment):
    artifact = stamp()
    primary = _conflicting_rows()
    grid_rows = form_rows()
    grid_rows[row_index]['variants'][1]['text'] = fragment
    before_rows, before_reads = deepcopy(grid_rows), deepcopy(_raw_reads(artifact))
    install_read_boundaries(monkeypatch, primary=primary,
                            directions=[{'direction': 0, 'rows': grid_rows}], accepted=True)
    assert _run(artifact, tmp_path) == []
    form = artifact['receiving_form']
    assert form['status'] == 'conflict'
    raw_direction = form['grid']['directions'][0]
    assert raw_direction['form_verified'] is True
    assert raw_direction['decision']['conflicts'][0]['reason'] == 'disagreeing_whole_row_observations'
    assert raw_direction['rows'][row_index]['variants'][1]['text'] == fragment
    audit = form['requirement_code_resolution']
    assert audit['accepted'] is False
    assert audit['original_block']['reason'] == 'primary_meaningful_conflict'
    assert audit['comparison_primary']['can_compare'] is True
    assert audit['comparison_grid_alternatives'] == []
    assert audit['comparison_selection']['reason'] == 'remaining_meaningful_identity_conflict'
    conflict = audit['comparison_selection']['remaining_conflicts'][0]
    assert conflict['source'] == 'grid[0]'
    assert any(value['text'] == fragment for value in conflict['conflicts'][0]['observations'])
    assert grid_rows == before_rows and _raw_reads(artifact) == before_reads


def test_only_a_complete_detector_row_can_be_adjusted_as_a_comparison_copy(monkeypatch, tmp_path):
    artifact = stamp()
    boxes = [{'row_index': 0, 'left': 60, 'text': '上海朱泾', 'confidence': .99},
             {'row_index': 0, 'left': 600, 'text': 'DCO21DCO', 'confidence': .99}]
    before = deepcopy(boxes)
    install_read_boundaries(monkeypatch, primary=form_rows(), detections=boxes)
    assert _run(artifact, tmp_path) == [IDENTITY]
    audit = artifact['receiving_form']['requirement_code_resolution']
    assert any(item['source'] == 'primary.detector_row[0]' for item in audit['adjustments'])
    assert boxes == before
    raw = artifact['receiving_form']['primary']['conflicts'][0]['observations']
    assert any(value['text'] == '上海朱泾DCO21DCO' for value in raw)
