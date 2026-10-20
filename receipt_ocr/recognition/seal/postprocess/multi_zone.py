"""Classify actual multi-zone OCR readings without consulting requirements."""
import re
from collections import Counter

from ..layout_fields import inner_role, phone_value


def finalize_multi_zone(artifact):
    if not artifact.get('multi_zone_layout'):
        return []
    rows = artifact.get('reads', [])
    centers, stations, numbers, names = [], [], [], []
    phones, observed_roles = [], []
    unknown_lower = False
    for row in rows:
        channel = row.get('channel')
        if channel not in {'layout_inner', 'layout_lower', 'ring'}:
            continue
        text = re.sub(r'\s+', '', row.get('text', ''))
        trusted = float(row.get('confidence') or 0) >= .80
        role = '待核对文字'
        row['matching'] = False
        if channel == 'ring':
            role = '上弧名称'
            if trusted:
                names.append(text)
        elif channel == 'layout_lower':
            phone = phone_value(text)
            role = '下弧电话' if phone else '下弧文字'
            artifact['lower_arc_text'] = row['text']
            if trusted and phone:
                phones.append(phone)
                artifact.update(phone_text=phone, phone_url=row['image_url'])
            else:
                unknown_lower = True
        elif trusted and phone_value(text):
            role = '章内电话'
            phone = phone_value(text)
            phones.append(phone)
            artifact.update(phone_text=phone, phone_url=row['image_url'])
        elif trusted and re.fullmatch(r'[（(]\d{1,3}[）)]', text):
            role = '章内编号'
            numbers.append(text)
            artifact.update(stamp_number_text=row['text'], stamp_number_url=row['image_url'])
        elif trusted and re.fullmatch(r'\d{4,12}站', text):
            role = '站点编号'
            stations.append(text)
            artifact.update(station_text=row['text'], station_url=row['image_url'])
        elif trusted and (text.endswith('章') or text in {'业务受理', '业务受理章'}):
            role = '章型文字'
            centers.append(text)
            artifact.update(round_type_band_text=row['text'], round_type_band_texts=[row['text']],
                            round_type_band_url=row['image_url'])
        row['zone_label'] = role
        if channel == 'layout_inner':
            observed_roles.append(inner_role(text) if trusted else '')
    # Assemble only unambiguous observations from this single physical stamp.
    # Number remains separately visible; phone never enters the company name.
    matching = []
    layout = artifact['multi_zone_layout']
    if layout.get('method') == 'ellipse_detected_rows':
        expected = layout.get('expected_inner_roles') or []
        lower_expected = 'lower' in layout.get('arcs', {})
        complete = (
            bool(expected) and Counter(observed_roles) == Counter(expected)
            and len(names) == len(centers) == 1 and len(phones) == 1
            and not stations and len(numbers) <= 1 and not unknown_lower
            and (not lower_expected or any(r.get('channel') == 'layout_lower' for r in rows))
        )
        if complete:
            bases = [names[0]+centers[0]]
            if numbers:
                bases.append(bases[0]+numbers[0])
            # Preserve the contact as a field. Also support requirements that
            # print its digits directly after the purpose without a label.
            for base in bases:
                matching.extend([base, base+'电话:'+phones[0],
                                 base+phones[0], base+phones[0].replace('-', '')])
            artifact['combined_text'] = bases[-1]+'电话:'+phones[0]
        else:
            artifact['combined_text'] = ''
    elif len(names) == len(centers) == len(stations) == 1:
        matching.append(names[0]+centers[0]+stations[0])
        if len(numbers) == 1:
            matching.append(names[0]+centers[0]+numbers[0]+stations[0])
    artifact['matching_texts'] = matching
    if layout.get('method') != 'ellipse_detected_rows':
        artifact['combined_text'] = matching[0] if matching else ''
    artifact['multi_zone_layout']['status'] = 'recognized' if matching else 'needs_review'
    return matching
