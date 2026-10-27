"""Combine only verified same-stamp station-code, telephone and company reads."""
import numpy as np

from ..preprocess.code_phone import valid_code_phone_fields


def finalize_code_phone(artifact):
    inputs = {item['id']: item.get('zone_name') for item in artifact.get('inputs', [])}
    labels = {'code': '上弧站代码', 'phone': '横排电话', 'company': '下弧公司名称'}
    values = {}
    valid = True
    for read in artifact.get('reads', []):
        name = inputs.get(read.get('input_id'))
        read['matching'] = False
        if name not in labels:
            valid = False
            continue
        read['zone_label'] = labels[name]
        score = float(read.get('confidence') or 0)
        if name in values or not np.isfinite(score) or score < .95 or not read.get('text'):
            valid = False
        values[name] = read
    complete = valid and set(values) == set(labels)
    if complete:
        complete = valid_code_phone_fields(*(values[name]['text'] for name in ('code', 'phone', 'company')))
        anchor = (artifact.get('orientation') or {}).get('anchor_text')
        complete = complete and values['phone']['text'].replace(' ', '') == anchor
    matching = []
    if complete:
        # All three strings were read from this stamp. Keep their digits and
        # punctuation exactly as observed, independent of the required wording.
        matching = [''.join(values[name]['text'] for name in ('code', 'phone', 'company'))]
        artifact.update(station_code_text=values['code']['text'], phone_text=values['phone']['text'],
                        phone_url=values['phone']['image_url'], unwrapped_text=values['company']['text'])
    artifact['matching_texts'] = matching
    artifact['combined_text'] = matching[0] if matching else ''
    artifact['multi_zone_layout']['status'] = 'recognized' if matching else 'needs_review'
    return matching
