"""Stable result revisions shared by queue writers and evidence maintenance."""
import hashlib
import json


def result_revision(row, *, legacy=False):
    values = [row[key] for key in ('result_json', 'review_status', 'final_result',
                                   'human_note', 'attempt', 'deleted_at')]
    if legacy:
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()
    payload = json.loads(values[0])
    # Page associations are a projection of the current batch, not a human
    # edit. Linking another page must not invalidate an otherwise safe retry.
    for key in ('page_group', 'page_group_id', 'page_role', 'parent_result_id', 'page_index'):
        payload.pop(key, None)
    values[0] = payload
    return 'v2:' + hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
