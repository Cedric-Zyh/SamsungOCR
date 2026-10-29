"""Auditable title-dot comparison copies, independent of identity adoption."""
from copy import deepcopy

from receipt_ocr.domain.parsing.parsing_seals import normalize_seal_text_strict
from receipt_ocr.domain.warehouse_punctuation_policy import warehouse_title_punctuation_adjustment
from .receiving_identity import (
    choose_receiving_identity, decide_receiving_identity, normalize_receiving_text,
    observation_confidence, verified_grid_alternatives,
)
from .receiving_purpose import resolved_purpose_covers

POLICY = 'warehouse_title_punctuation_from_requirement'


def _title_index(rows, purpose):
    if not isinstance(purpose, int) or isinstance(purpose, bool):
        return None
    for row in sorted(rows, key=lambda value: value['row_index']):
        if row['row_index'] >= purpose:
            break
        if row.get('is_blank') is True and (row.get('blank_check') or {}).get('blank') is True:
            continue
        return row['row_index']
    return None


def _adjust(value, requirement, changes, source):
    if observation_confidence(value.get('confidence')) < .75:
        return
    adjustment = warehouse_title_punctuation_adjustment(requirement, value.get('text'), title_only=True)
    if adjustment:
        changes.append(dict(adjustment, source=source))
        value['text'] = adjustment['text']


def _adjust_rows(rows, title, requirement, changes, source):
    for row in rows:
        if row['row_index'] != title:
            continue
        _adjust(row, requirement, changes, f'{source}.row[{title}]')
        for index, variant in enumerate(row.get('variants') or []):
            _adjust(variant, requirement, changes, f'{source}.row[{title}].variant[{index}]')


def _identity_values(rows, purpose):
    if isinstance(purpose, int) and not isinstance(purpose, bool):
        for row in rows:
            if row['row_index'] <= purpose:
                yield row
                yield from (dict(value, row_index=row['row_index']) for value in row.get('variants') or [])


def resolve_receiving_punctuation(requirement, primary, rows, detections, grid, *,
                                   old_form, older_observations, selection):
    """Expose a cleaned complete title even when another identity row blocks.

    Only an already proven form's first nonblank identity row is adjusted.
    Raw OCR, physical row mapping, and original decisions remain unchanged.
    ``candidate_text`` is a display preview, never an extra matching candidate.
    """
    if not isinstance(requirement, str) or not requirement:
        return None
    proven = [direction for direction in grid.get('directions') or []
              if grid.get('accepted') is True and direction.get('form_verified') is True]
    if not old_form and not proven:
        return None
    copied_rows, boxes = deepcopy(rows), deepcopy(detections)
    copied_grid, older = deepcopy(grid), deepcopy(older_observations)
    copied_grid['directions'] = deepcopy(proven)
    changes = []
    purpose = primary.get('purpose_row_index')
    title = _title_index(copied_rows, purpose) if old_form else None
    if title is not None:
        _adjust_rows(copied_rows, title, requirement, changes, 'primary')
        for index, box in enumerate(boxes):
            if box.get('row_index') == title:
                _adjust(box, requirement, changes, f'primary.detection[{index}]')
        for index, value in enumerate(older):
            if value.get('rectangle_row_index', value.get('row_index')) == title:
                _adjust(value, requirement, changes, f'older[{index}]')
    revised_primary = decide_receiving_identity(copied_rows, detected_boxes=boxes, grid_verified=old_form)
    for index, direction in enumerate(copied_grid['directions']):
        grid_purpose = direction['decision'].get('purpose_row_index')
        grid_title = _title_index(direction['rows'], grid_purpose)
        _adjust_rows(direction['rows'], grid_title, requirement, changes, f'grid[{index}]')
        direction['decision'] = decide_receiving_identity(direction['rows'], grid_verified=True)
    if not changes:
        return None
    alternatives = verified_grid_alternatives([copied_grid])
    revised = choose_receiving_identity(revised_primary, copied_rows, alternatives, older_observations=older)
    decisions = [('primary', revised_primary), *[
        (f'grid[{index}]', direction['decision'])
        for index, direction in enumerate(copied_grid['directions'])]]
    remaining = [dict(source=source, conflicts=deepcopy(conflicts))
                 for source, decision in decisions
                 if (conflicts := [conflict for conflict in decision.get('conflicts') or []
                                   if conflict.get('reason') != 'unread_nonblank_or_unknown_row'])]
    accepted = revised.get('can_compare') is True
    if remaining:
        accepted = False
        revised = dict(revised, can_compare=False, reason='remaining_meaningful_identity_conflict',
                       remaining_conflicts=remaining)
    if accepted and normalize_seal_text_strict(revised['text']) != normalize_seal_text_strict(requirement):
        accepted = False
        revised = dict(revised, can_compare=False, reason='complete_identity_not_requirement_compatible')
    values = [(value, revised_primary) for value in
              _identity_values(copied_rows, revised_primary.get('purpose_row_index'))]
    values.extend((box, revised_primary) for box in boxes if box.get('row_index') == title and title is not None)
    for direction in copied_grid['directions']:
        values.extend((value, direction['decision']) for value in
                      _identity_values(direction['rows'], direction['decision'].get('purpose_row_index')))
    values.extend((value, revised_primary) for value in older)
    if accepted:
        text = normalize_receiving_text(revised['text'])
        conflicts = [value for value, decision in values if observation_confidence(value.get('confidence')) >= .75
                     and normalize_receiving_text(value.get('text'))
                     and normalize_receiving_text(value.get('text')) not in text
                     and not resolved_purpose_covers(value, decision, revised['text'])]
        if conflicts:
            accepted = False
            revised = dict(revised, can_compare=False, reason='remaining_reliable_identity_conflict',
                           observations=deepcopy(conflicts))
    blocked = dict(selection, text='', can_compare=False, source=None, reason=revised['reason'])
    return {'selection': revised if accepted else blocked, 'candidate_text': changes[0]['text'],
            'audit': {'policy': POLICY, 'applied': True, 'accepted': accepted,
                      'adjustments': changes, 'original_selection': deepcopy(selection),
                      'original_primary': deepcopy(primary),
                      'original_grid_decisions': [deepcopy(d.get('decision')) for d in proven],
                      'comparison_primary': revised_primary,
                      'comparison_grid_decisions': [d['decision'] for d in copied_grid['directions']],
                      'comparison_selection': revised, 'comparison_grid_alternatives': alternatives}}
