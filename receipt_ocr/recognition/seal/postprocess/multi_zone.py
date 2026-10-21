"""Classify actual multi-zone OCR readings without consulting requirements."""
import re
from collections import Counter

from ..layout_fields import inner_role, phone_value


def _station_phone(text):
    phone = phone_value(text)
    if phone:
        return phone
    # Labelled, contiguous landlines only. Keep the actual digits; a high OCR
    # score does not make a short/partial phone number complete.
    match = re.fullmatch(r'电话[:：]((?:010|02\d)\d{8}|0[3-9]\d{2}\d{7,8})', text)
    return match[1] if match else ''


def finalize_multi_zone(artifact):
    if not artifact.get('multi_zone_layout'):
        return []
    rows = artifact.get('reads', [])
    centers, stations, numbers, names = [], [], [], []
    phones, observed_roles = [], []
    unknown_lower = False
    station_layout = artifact['multi_zone_layout'].get('method') == 'ellipse_station_rows'
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
            phone = _station_phone(text) if station_layout else phone_value(text)
            role = '下弧电话' if phone else '下弧文字'
            artifact['lower_arc_text'] = row['text']
            if trusted and phone:
                phones.append(phone)
                artifact.update(phone_text=phone, phone_url=row['image_url'])
            else:
                unknown_lower = True
                if station_layout:
                    role = '下弧电话（识别不完整，请核对）'
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
    elif station_layout:
        complete = (Counter(observed_roles) == Counter(['number', 'type', 'station'])
                    and len(names) == len(centers) == len(stations) == len(numbers) == 1)
        if complete:
            base = names[0]+centers[0]+numbers[0]+stations[0]
            matching.append(base)
            if len(phones) == 1 and not unknown_lower:
                matching.extend([base+'电话:'+phones[0], base+phones[0].replace('-', '')])
            artifact['combined_text'] = matching[1] if len(matching) > 1 else base
        else:
            artifact['combined_text'] = ''
        artifact['phone_status'] = ('recognized' if len(phones) == 1 and not unknown_lower
                                    else 'needs_review')
    elif len(names) == len(centers) == len(stations) == 1:
        matching.append(names[0]+centers[0]+stations[0])
        if len(numbers) == 1:
            matching.append(names[0]+centers[0]+numbers[0]+stations[0])
    artifact['matching_texts'] = matching
    if layout.get('method') not in {'ellipse_detected_rows', 'ellipse_station_rows'}:
        artifact['combined_text'] = matching[0] if matching else ''
    artifact['multi_zone_layout']['status'] = 'recognized' if matching else 'needs_review'
    if station_layout and artifact['phone_status'] == 'needs_review':
        artifact['multi_zone_layout']['status'] = 'needs_review'
    return matching
