"""Title-dot cleanup composes with a narrowly resolved observed purpose."""
from copy import deepcopy

import pytest

from receipt_ocr.recognition.seal import receiving
from test_receiving_recovery import install_read_boundaries, physical_rows, stamp

TITLE = '北京永乐DC010DCM'
PURPOSE = '收货专用章②'
REQUIREMENT = TITLE + PURPOSE


def _run(artifact, tmp_path, requirement=REQUIREMENT):
    return receiving.recognize_receiving_form(
        artifact, tmp_path, '/files/test', 'paddle_v6', object(),
        orientation_mode='combined', requirement=requirement)


def _conflicting_fixture(monkeypatch, punctuation='·'):
    title = TITLE.replace('DC010', f'DC{punctuation}010')
    rows = physical_rows([title, '收费专用章②', '收货人', '日期'])
    rows[1]['variants'][1]['text'] = '收费专用章'
    boxes = [{'row_index': 0, 'text': title, 'confidence': .99},
             {'row_index': 1, 'text': '收货专用章', 'confidence': .99}]
    artifact = stamp(subject=title)
    artifact['reads'] = [dict(row, channel='body', rectangle_row_index=row['row_index'])
                         for row in rows]
    # As in the real sample, the older mapped detector observes the purpose
    # even though the row OCR reads 收费; it enables the existing form route.
    artifact['rectangular_frame'] = {'detected_boxes': deepcopy(boxes)}
    install_read_boundaries(monkeypatch, primary=rows, detections=boxes)
    return artifact, rows, boxes


@pytest.mark.parametrize('punctuation', ['.', '·'])
def test_title_cleanup_retains_same_row_resolved_purpose_without_rewriting_raw_evidence(
        monkeypatch, tmp_path, punctuation):
    artifact, rows, boxes = _conflicting_fixture(monkeypatch, punctuation)
    originals = deepcopy((artifact['reads'], rows, boxes))
    assert _run(artifact, tmp_path) == [REQUIREMENT]
    form = artifact['receiving_form']
    assert form['candidate_text'] == REQUIREMENT
    assert form['identity_text'] == REQUIREMENT and form['status'] == 'adopted'
    assert form['primary']['identity_rows'][0]['text'] == rows[0]['text']
    assert form['primary']['conflicts'] == []
    assert form['primary']['purpose_resolution']['number_text'] == '②'
    audit = form['requirement_title_punctuation']
    assert audit['applied'] is True and audit['accepted'] is True
    assert audit['policy'] == 'warehouse_title_punctuation_from_requirement'
    assert audit['adjustments'][0]['raw_text'] == rows[0]['text']
    assert audit['comparison_primary']['identity_rows'][0]['text'] == TITLE
    assert audit['comparison_primary']['conflicts'] == []
    assert audit['original_primary'] == form['primary']
    raw_reads = [{k: v for k, v in row.items() if k != 'matching'} for row in artifact['reads']]
    assert (raw_reads, rows, boxes) == originals
    check = {'status': '匹配', 'recognized': REQUIREMENT, 'reliable': True}
    assert receiving.apply_receiving_review(check, [artifact]) == check
    assert 'receiving_review' not in check


def test_only_the_new_comparison_copy_can_adopt_a_consistent_complete_identity(monkeypatch, tmp_path):
    title = TITLE.replace('DC010', 'DC·010')
    rows = physical_rows([title, PURPOSE, '收货人', '日期'])
    rows[0]['variants'][1]['text'] = TITLE
    artifact = stamp(subject=title)
    artifact['reads'] = [dict(rows[1], channel='body', rectangle_row_index=1)]
    install_read_boundaries(monkeypatch, primary=rows)
    assert _run(artifact, tmp_path) == [REQUIREMENT]
    form = artifact['receiving_form']
    assert form['primary']['conflicts']
    assert form['requirement_title_punctuation']['accepted'] is True
    assert form['requirement_title_punctuation']['comparison_primary']['conflicts'] == []
    assert rows[0]['text'] == title


def test_a_verified_grid_title_can_be_adjusted_without_relabelling_raw_rows(monkeypatch, tmp_path):
    title = TITLE.replace('DC010', 'DC.010')
    rows = physical_rows([title, PURPOSE, '收货人', '日期'])
    rows[0]['variants'][1]['text'] = TITLE
    artifact = stamp(frame=False)
    install_read_boundaries(monkeypatch, directions=[{'direction': 0, 'rows': rows}], accepted=True)
    assert _run(artifact, tmp_path) == [REQUIREMENT]
    form = artifact['receiving_form']
    assert form['selected_source'] == 'verified_grid_recovery'
    assert form['grid']['directions'][0]['rows'][0]['text'] == title
    assert form['requirement_title_punctuation']['comparison_grid_alternatives'][0]['dual_representation_verified']


def test_another_verified_whole_row_conflict_cannot_hide_behind_primary_precedence(monkeypatch, tmp_path):
    title = TITLE.replace('DC010', 'DC·010')
    rows = physical_rows([title, PURPOSE, '收货人', '日期'])
    grid_rows = physical_rows([TITLE, PURPOSE, '收货人', '日期'])
    grid_rows[1]['variants'][1]['text'] = '010'
    artifact = stamp(subject=title)
    artifact['reads'] = []
    install_read_boundaries(monkeypatch, primary=rows,
                            directions=[{'direction': 0, 'rows': grid_rows}], accepted=True)
    assert _run(artifact, tmp_path) == []
    audit = artifact['receiving_form']['requirement_title_punctuation']
    assert audit['applied'] is True and audit['accepted'] is False
    assert audit['comparison_selection']['reason'] == 'remaining_meaningful_identity_conflict'
    assert artifact['matching_texts'] == []


@pytest.mark.parametrize('requirement', ['', REQUIREMENT.replace('永乐', '另一'),
                                       REQUIREMENT.replace('010', '011')])
def test_missing_or_incompatible_requirement_does_not_clean_the_title(monkeypatch, tmp_path, requirement):
    artifact, rows, _ = _conflicting_fixture(monkeypatch)
    assert _run(artifact, tmp_path, requirement) == [rows[0]['text'] + PURPOSE]
    assert artifact['receiving_form']['candidate_text'] == rows[0]['text'] + PURPOSE
    assert 'requirement_title_punctuation' not in artifact['receiving_form']


def test_unverified_form_does_not_get_a_requested_title_preview(monkeypatch, tmp_path):
    title = TITLE.replace('DC010', 'DC·010')
    artifact = stamp(frame=False)
    rows = physical_rows([title, '收费专用章②', '收货人', '日期'])
    install_read_boundaries(monkeypatch, directions=[{'direction': 0, 'rows': rows}], accepted=True)
    assert _run(artifact, tmp_path) is None
    assert 'receiving_form' not in artifact
