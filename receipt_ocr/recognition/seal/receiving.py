"""Conservative receiving-form recovery and auditable split candidates.

Complete physical rows and their observed purpose establish the identity.
An optional requested-code policy runs only after raw selection is blocked;
it never enters the OCR or geometric evidence boundaries.
"""
from copy import deepcopy
from pathlib import Path
import re

from .ocr.receiving import read_primary, read_grid, read_input
from .preprocess.receiving_grid import prepare_receiving_grid
from .preprocess.receiving_split import prepare_receiving_split
from .postprocess.receiving_split import decide_receiving_split
from .postprocess.receiving_code_policy import resolve_receiving_code
from .postprocess.receiving_punctuation import resolve_receiving_punctuation
from .postprocess.receiving_identity import (
    choose_receiving_identity, decide_receiving_identity, older_identity_observations,
    observation_confidence, verified_grid_alternatives,
)


def _public(value):
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()
                if key != 'path' and not key.endswith('_path')}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _observations(artifact):
    yield from artifact.get('reads') or []
    for key in ['rectangular_frame', 'orientation']:
        yield from (artifact.get(key) or {}).get('detected_boxes') or []


def _has_purpose(observations):
    return any('收货专用章' in str(row.get('text') or '')
               and observation_confidence(row.get('confidence')) >= .75 for row in observations)


def _has_caption(observations):
    label = r'(?:收货人|收货日期|收货时间|签收人|签收日期|日期)'
    pattern = re.compile(label + r'(?:[.:：。]?|[:：].{1,80}|[0-9０-９年月日时分秒/:.－—-]{1,40})')
    return any(pattern.fullmatch(re.sub(r'\s+', '', str(row.get('text') or '')))
               and observation_confidence(row.get('confidence')) >= .75 for row in observations)


def _proven_form(rows, decision, detected_boxes=()):
    """Prove this direction's form roles; a date value need not be readable."""
    purpose = decision.get('purpose_row_index')
    if not isinstance(purpose, int) or isinstance(purpose, bool):
        return False
    after = [row for row in rows if row['row_index'] > purpose]
    if not after:
        return False
    def values(row):
        return [row, *(row.get('variants') or []),
                *(box for box in detected_boxes if box.get('row_index') == row['row_index'])]
    recipients = [row['row_index'] for row in after if any(
        _has_caption([v]) and re.match(r'^(收货人|签收人)', re.sub(r'\s+', '', str(v.get('text') or '')))
        for v in values(row))]
    if not recipients:
        return False
    caption_seen = False
    for row in after:
        observations = values(row)
        date_position = (len(after) == 2 and row is after[-1]
                         and recipients and max(recipients) < row['row_index'])
        # The measured form and the observed recipient establish the last
        # cell's role. Its date value, including unreadable OCR, is irrelevant
        # to the seal identity and must not block comparison.
        if date_position:
            row['ignored_date'] = True
            continue
        if _has_caption(observations):
            caption_seen = True
            continue
        if row.get('is_blank') is True and (row.get('blank_check') or {}).get('blank') is True:
            continue
        return False
    return caption_seen


def _grid_directions(artifact, grid, directory, prefix, provider, reader):
    if not grid.get('accepted'):
        return []
    directions = read_grid(artifact, grid, directory, prefix, provider, reader)
    for direction in directions:
        direction['decision'] = decide_receiving_identity(direction['rows'], grid_verified=True)
    return directions


def _split_direction(directions):
    eligible = []
    for direction in directions:
        if direction.get('form_verified') is not True:
            continue
        purposes = []
        for row in direction['rows']:
            views = row['variants']
            if (len(views) == 2 and all('收货专用章' in v['text'] and v['confidence'] >= .75 for v in views)
                    and re.sub(r'\s+', '', views[0]['text']) == re.sub(r'\s+', '', views[1]['text'])):
                purposes.append(row)
        if len(purposes) == 1 and purposes[0]['row_index'] == 1:
            eligible.append((direction, purposes[0]))
    return eligible[0] if len(eligible) == 1 else None


def _read_split(artifact, grid, directions, original, directory, prefix, provider, reader):
    selected = _split_direction(directions)
    if selected is None:
        return None
    direction, purpose = selected
    # Split only a measured top identity row. Both actual representations
    # use identical geometry, and every resulting read stays in the audit.
    top = direction['rows'][0]
    if not any(re.search(r'[A-Za-z0-9]', value['text']) for value in top['variants']):
        return None
    if direction['direction'] != 0:
        # A rotated recovery may be compared, but its split inputs need an
        # explicitly aligned pair before the optional title probe can run.
        return None
    metadata, jobs = prepare_receiving_split(
        grid['rows'][0]['color_path'], grid['rows'][0]['gray_path'], directory,
        artifact['index'], prefix, row_index=0, grid_verified=True,
    )
    for item, path in jobs:
        read_input(artifact, reader, provider, path, item)
    old = [row for row in original.get('reads') or []
           if row.get('rectangle_row_index') == 0 and row.get('channel') == 'body']
    old += top['variants']
    decision = decide_receiving_split(metadata, artifact['reads'], whole_row_reads=old)
    decision['inputs'] = metadata['inputs']
    if decision.get('accepted'):
        decision['identity_text'] = decision['title_text'] + purpose['text']
    return decision


def recognize_receiving_form(artifact, directory, prefix, provider, reader, *, orientation_mode,
                              requirement=''):
    """Return matching texts for a proven receiving form, else ``None``.

    Existing non-form stamps retain their established path. Split readings
    never silently replace whole-row disagreements or become extra matching
    candidates. Real observations and failures remain in the same artifact.
    """
    if provider != 'paddle_v6' or artifact.get('color') != 'red' or orientation_mode == 'none':
        return None
    # Older/minimal artifacts without physical crop provenance keep their
    # established route; recovery needs an actual indexed stamp image.
    if not isinstance(artifact.get('index'), int):
        return None
    original = deepcopy(artifact)
    observations = list(_observations(original))
    source = Path(directory) / (Path(original.get('original_url', '')).name
                                or f"seal-{artifact['index']}-original.png")
    grid = None
    if not _has_purpose(observations):
        # A complete physical grid can recover a stamp misrouted as an
        # ellipse before any usable purpose text was read. It still needs
        # actual same-direction purpose and recipient observations below.
        try:
            grid = prepare_receiving_grid(source, directory, artifact['index'], prefix)
        except Exception:
            return None
        if not grid.get('accepted'):
            return None
    frame_rows = original.get('rectangular_layout') == 'frame_rows'
    rows, detections = read_primary(artifact, original, directory, prefix, provider, reader)
    primary = decide_receiving_identity(rows, detected_boxes=detections,
                                        grid_verified=frame_rows and len(rows) >= 3)
    old_form = frame_rows and _proven_form(rows, primary, detections)
    if not old_form:
        primary = decide_receiving_identity(rows, detected_boxes=detections, grid_verified=False)
    try:
        if grid is None:
            grid = prepare_receiving_grid(source, directory, artifact['index'], prefix)
        directions = _grid_directions(artifact, grid, directory, prefix, provider, reader)
    except Exception as exc:
        artifact['errors'].append({'step': 'receiving_grid', 'message': str(exc)})
        grid, directions = {'accepted': False, 'reason': 'grid_recovery_failed'}, []
    for direction in directions:
        direction['form_verified'] = _proven_form(direction['rows'], direction['decision'])
    grid.update(directions=directions)
    proven_directions = [direction for direction in directions if direction['form_verified']]
    if not old_form and not proven_directions:
        return None
    alternatives = verified_grid_alternatives([{**grid, 'directions': proven_directions}])
    selection = choose_receiving_identity(primary, rows, alternatives,
                                          older_observations=older_identity_observations(original))
    try:
        split = _read_split(artifact, grid, directions, original, directory, prefix, provider, reader)
    except Exception as exc:
        artifact['errors'].append({'step': 'receiving_split', 'message': str(exc)})
        split = {'accepted': False, 'requires_review': True, 'matching_allowed': False,
                 'reason': 'split_recovery_failed'}
    code_resolution = resolve_receiving_code(
        requirement, primary, rows, detections, grid, old_form=old_form,
        older_observations=older_identity_observations(original), selection=selection)
    if code_resolution:
        selection = code_resolution['selection']
    title_punctuation = resolve_receiving_punctuation(
        requirement, primary, rows, detections, grid, old_form=old_form,
        older_observations=older_identity_observations(original), selection=selection)
    if title_punctuation:
        selection = title_punctuation['selection']
    identity = selection['text'] if selection['can_compare'] else ''
    candidate = (split or {}).get('identity_text') or primary.get('identity_text') or ''
    if title_punctuation:
        candidate = title_punctuation['candidate_text']
    status = 'adopted' if identity else 'conflict' if primary.get('conflicts') else 'insufficient'
    artifact['receiving_form'] = _public({
        'schema_version': 1, 'managed': True, 'status': status,
        'identity_text': identity, 'candidate_text': candidate,
        'selected_source': selection['source'], 'reason': selection['reason'],
        'primary': primary, 'grid': grid, 'split': split,
        'field_rows': primary.get('form_rows', []),
        'date_values_ignored': True,
    })
    if code_resolution:
        artifact['receiving_form']['requirement_code_resolution'] = _public(code_resolution['audit'])
    if title_punctuation:
        artifact['receiving_form']['requirement_title_punctuation'] = _public(title_punctuation['audit'])
    # All original strings remain visible, including a blocked exact-looking
    # candidate. Only this physical stamp's selected identity enters matching.
    for read in artifact['reads']:
        read['matching'] = False
    artifact['matching_texts'] = [identity] if identity else []
    if selection['source'] == 'verified_grid_recovery':
        artifact['shape_before_receiving_grid'] = artifact.get('shape')
        artifact['receiving_selected_image_url'] = grid['whole_color_url']
        artifact.update(shape='矩形', shape_code='rectangle')
    return artifact['matching_texts']


def apply_receiving_review(check, artifacts):
    """Explain withheld evidence without changing another stamp's exact match."""
    forms = [a['receiving_form'] for a in artifacts if a.get('receiving_form', {}).get('managed')]
    number_conflicts = [a['receiving_number'] for a in artifacts
                        if (a.get('receiving_number') or {}).get('status') == 'conflict']
    if check.get('status') == '匹配':
        return check
    blocked = [form for form in forms if form['status'] != 'adopted']
    if not blocked and not number_conflicts:
        return check
    candidate = max((form.get('candidate_text', '') for form in blocked), key=len, default='')
    if not check.get('recognized') and candidate:
        check.update(recognized=candidate, display_text=candidate)
    check['receiving_review'] = {
        'required': True, 'reasons': [form['reason'] for form in blocked]
                                   + [item['reason'] for item in number_conflicts],
        'message': ('同一用途行的章号读数存在分歧，请核对原始圈号与补读图' if number_conflicts
                    else '收货章身份文字存在分歧或证据不足，请核对逐行读数与分段候选'),
    }
    if any((form.get('requirement_title_punctuation') or {}).get('applied') is True for form in blocked):
        check['receiving_review']['message'] = (
            '首行代码前的多余分隔点已按签章要求剔除；' + check['receiving_review']['message'])
    check.update(reliable=False, message=check['receiving_review']['message'])
    return check
