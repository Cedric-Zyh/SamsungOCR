"""Late warehouse-code comparison policy, after physical receiving-form proof."""
from copy import deepcopy

from receipt_ocr.domain.parsing.parsing_seals import normalize_seal_text_strict
from receipt_ocr.domain.warehouse_code_policy import warehouse_code_adjustment
from .receiving_identity import (
    choose_receiving_identity, decide_receiving_identity, normalize_receiving_text,
    observation_confidence, verified_grid_alternatives,
)
from .receiving_purpose import resolved_purpose_covers


def _correct_observation(value, requirement, adjustments, source):
    if observation_confidence(value.get('confidence')) < .75:
        return
    for title_only in (True, False):
        adjustment = warehouse_code_adjustment(requirement, value.get('text'), title_only=title_only)
        if adjustment:
            adjustments.append(dict(adjustment, source=source, title_only=title_only))
            value['text'] = adjustment['text']
            return


def _correct_rows(rows, purpose, requirement, adjustments, source):
    if not isinstance(purpose, int) or isinstance(purpose, bool):
        return
    for row in rows:
        if row['row_index'] >= purpose:
            continue
        path = f"{source}.row[{row['row_index']}]"
        _correct_observation(row, requirement, adjustments, path)
        for index, variant in enumerate(row.get('variants') or []):
            _correct_observation(variant, requirement, adjustments, f'{path}.variant[{index}]')


def _correct_detections(boxes, rows, purpose, requirement, adjustments):
    if not isinstance(purpose, int) or isinstance(purpose, bool):
        return boxes
    indexes = {row['row_index'] for row in rows if row['row_index'] < purpose}
    for index, box in enumerate(boxes):
        if box.get('row_index') in indexes:
            _correct_observation(box, requirement, adjustments, f'primary.detection[{index}]')
    # A detector may split the complete title into several boxes. Only its
    # already mapped whole physical row may be adjusted; a code fragment alone
    # never receives an O/0 replacement.
    replacements = {}
    for row_index in indexes:
        group = [box for box in boxes if box.get('row_index') == row_index
                 and observation_confidence(box.get('confidence')) >= .75 and box.get('text')]
        if len(group) < 2:
            continue
        def coordinate(box):
            try:
                return float(box.get('left', box.get('x', 0)) or 0)
            except (TypeError, ValueError, OverflowError):
                return 0.0
        group.sort(key=coordinate)
        composite = dict(group[0], text=''.join(str(box['text']) for box in group),
                         confidence=min(observation_confidence(box.get('confidence')) for box in group))
        count = len(adjustments)
        _correct_observation(composite, requirement, adjustments, f'primary.detector_row[{row_index}]')
        if len(adjustments) > count:
            replacements[row_index] = composite
    out, emitted = [], set()
    for box in boxes:
        index = box.get('row_index')
        if index not in replacements:
            out.append(box)
        elif index not in emitted:
            out.append(replacements[index])
            emitted.add(index)
    return out


def _identity_observations(rows, purpose):
    if not isinstance(purpose, int) or isinstance(purpose, bool):
        return
    for row in rows:
        if row['row_index'] <= purpose:
            yield row
            yield from (dict(value, row_index=row['row_index']) for value in row.get('variants') or [])


def resolve_receiving_code(requirement, primary, rows, detections, grid, *,
                           old_form, older_observations, selection):
    """Retry blocked selection on comparison copies; never change raw evidence.

    Complete title/name/code observations may resolve only the requested O/0
    policy. The selected complete identity must still match the requirement,
    and every reliable non-value observation must remain compatible. In
    particular an uncorrectable code fragment retains its blocking force.
    """
    if not isinstance(requirement, str) or not requirement or selection.get('can_compare'):
        return None
    proven = [direction for direction in grid.get('directions') or []
              if direction.get('form_verified') is True]
    if not old_form and not (grid.get('accepted') is True and proven):
        return None
    adjusted_rows, adjusted_boxes = deepcopy(rows), deepcopy(detections)
    adjusted_old, adjusted_grid = deepcopy(older_observations), deepcopy(grid)
    adjusted_grid['directions'] = deepcopy(proven)
    adjustments = []
    purpose = primary.get('purpose_row_index')
    _correct_rows(adjusted_rows, purpose, requirement, adjustments, 'primary')
    adjusted_boxes = _correct_detections(adjusted_boxes, adjusted_rows, purpose, requirement, adjustments)
    revised_primary = decide_receiving_identity(adjusted_rows, detected_boxes=adjusted_boxes,
                                                grid_verified=old_form)
    for index, direction in enumerate(adjusted_grid['directions']):
        purpose_index = (direction.get('decision') or {}).get('purpose_row_index')
        _correct_rows(direction['rows'], purpose_index, requirement, adjustments, f'grid[{index}]')
        direction['decision'] = decide_receiving_identity(direction['rows'], grid_verified=True)
    for index, value in enumerate(adjusted_old):
        _correct_observation(value, requirement, adjustments, f'older[{index}]')
    if not adjustments:
        return None
    alternatives = verified_grid_alternatives([adjusted_grid])
    revised = choose_receiving_identity(revised_primary, adjusted_rows, alternatives,
                                        older_observations=adjusted_old)
    # The normal selector deliberately prioritizes an already usable primary.
    # This late fallback is stricter: it must not hide a separate remaining
    # name, numeric, stamp-number, or fragment conflict while resolving O/0.
    observations = [(value, revised_primary) for value in
                    _identity_observations(adjusted_rows, revised_primary.get('purpose_row_index'))]
    indexes = {row['row_index'] for row in revised_primary.get('identity_rows') or []}
    observations.extend((box, revised_primary) for box in adjusted_boxes if box.get('row_index') in indexes)
    for direction in adjusted_grid['directions']:
        observations.extend((value, direction['decision']) for value in _identity_observations(
            direction['rows'], direction['decision'].get('purpose_row_index')))
    observations.extend((value, revised_primary) for value in adjusted_old)
    accepted = revised.get('can_compare') is True
    decisions = [('primary', revised_primary), *[
        (f'grid[{index}]', direction['decision'])
        for index, direction in enumerate(adjusted_grid['directions'])]]
    remaining = [dict(source=source, conflicts=deepcopy(conflicts))
                 for source, decision in decisions
                 if (conflicts := [conflict for conflict in decision.get('conflicts') or []
                                   if conflict.get('reason') != 'unread_nonblank_or_unknown_row'])]
    # A reliable whole-row disagreement stays a disagreement even when its
    # shorter string happens to occur somewhere in the complete identity.
    # The primary-first selector must not bypass such a verified grid conflict.
    if remaining:
        accepted = False
        revised = dict(revised, can_compare=False, reason='remaining_meaningful_identity_conflict',
                       remaining_conflicts=remaining)
    if accepted and normalize_seal_text_strict(revised['text']) != normalize_seal_text_strict(requirement):
        accepted = False
        revised = dict(revised, can_compare=False, reason='complete_identity_not_requirement_compatible')
    if accepted:
        text = normalize_receiving_text(revised['text'])
        conflicts = [value for value, decision in observations
                     if observation_confidence(value.get('confidence')) >= .75
                     and normalize_receiving_text(value.get('text'))
                     and normalize_receiving_text(value.get('text')) not in text
                     and not resolved_purpose_covers(value, decision, revised['text'])]
        if conflicts:
            accepted = False
            revised = dict(revised, can_compare=False, reason='remaining_reliable_identity_conflict',
                           observations=deepcopy(conflicts))
    return {'selection': revised if accepted else selection,
            'audit': {'policy': 'warehouse_code_zero_o_from_requirement', 'accepted': accepted,
                      'adjustments': adjustments, 'original_block': deepcopy(selection),
                      'comparison_selection': revised, 'comparison_primary': revised_primary,
                      'comparison_grid_alternatives': alternatives}}
