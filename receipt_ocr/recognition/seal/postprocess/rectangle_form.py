"""Keep receiving-form value cells out of the stamp identity comparison."""
import math
import re


_CAPTION = re.compile(r'(?:收货人|收货日期|收货时间|签收人|签收日期|日期)[:：]?')


def _caption_label(text):
    if _CAPTION.fullmatch(text):
        return text.rstrip(':：')
    filled = re.fullmatch(r'(收货人|签收人)[:：].{1,80}', text)
    date = re.fullmatch(r'(收货日期|签收日期|收货时间|日期)[:：]?[0-9０-９年月日时分秒/:.－—-]{1,40}', text)
    return (filled or date)[1] if filled or date else ''


def _receiving_value_rows(artifact, reads):
    """Map independently detected captions to complete physical form rows.

    A filled value or OCR noise may be joined to the caption by the line
    recognizer. The earlier detector still locates the printed caption, so
    the whole cell can be excluded without editing or guessing its value.
    """
    frame = artifact.get('rectangular_frame') or {}
    crop = frame.get('crop_box') or []
    prepared = artifact.get('rectangular_fixed_rows') or []
    purposes = [r.get('rectangle_row_index', -1) for r in reads
                if '收货专用章' in r.get('text', '') and r.get('confidence', 0) >= .75]
    purposes = [i for i in purposes if isinstance(i, int) and 0 <= i < len(prepared)]
    if not purposes or len(crop) != 4 or not all(math.isfinite(float(v)) for v in crop):
        return {}
    left, top, right, bottom = map(float, crop)
    width, height = right-left, bottom-top
    canonical_width = float(frame.get('canonical_width') or 0)
    padding = float(frame.get('padding', 32))
    if width <= 0 or height <= 0 or not math.isfinite(canonical_width) or canonical_width <= 0:
        return {}
    scaled_height = round(height*canonical_width/width)
    if scaled_height <= 0:
        return {}
    found = {}
    for box in frame.get('detected_boxes') or []:
        text = re.sub(r'\s+', '', str(box.get('text') or ''))
        caption = _caption_label(text)
        score = float(box.get('confidence') or 0)
        points = box.get('points') or []
        if (not caption or not math.isfinite(score) or score < .90
                or len(points) != 4 or any(len(p) != 2 for p in points)
                or not all(math.isfinite(float(v)) for p in points for v in p)):
            continue
        xs = [left+(float(p[0])-padding)*width/canonical_width for p in points]
        ys = [top+(float(p[1])-padding)*height/scaled_height for p in points]
        if max(xs) <= min(xs) or max(ys) <= min(ys):
            continue
        # These captions sit at the left of a separate row below the purpose.
        if min(xs) < left-2 or min(xs) > left+width*.35 or max(xs) > right+2:
            continue
        for index, row in enumerate(prepared):
            bounds = row.get('crop_box') or []
            if (index <= max(purposes) or row.get('coordinate_space') != 'oriented'
                    or len(bounds) != 4):
                continue
            if bounds[1] <= min(ys) and max(ys) <= bounds[3]:
                found[index] = caption
    return found


def mark_receiving_form_fields(artifact):
    if artifact.get('rectangular_layout') != 'frame_rows':
        return
    reads = [row for row in artifact.get('reads', []) if row.get('channel') == 'body']
    # A date stamp or an ordinary company name is not a receiving form. This
    # route needs its independently observed printed receiving-stamp purpose.
    if not any('收货专用章' in row.get('text', '') and row.get('confidence', 0) >= .75
               for row in reads):
        return
    value_rows = _receiving_value_rows(artifact, reads)
    fields = []
    for row in reads:
        text = re.sub(r'\s+', '', row.get('text', ''))
        index = row.get('rectangle_row_index', -1)
        prepared = artifact.get('rectangular_fixed_rows', [])
        verified_empty = prepared[index].get('empty_form_field') if 0 <= index < len(prepared) else None
        if (index in value_rows or verified_empty
                or re.fullmatch(r'(?:收货人|收货日期|收货时间|签收人|签收日期|日期)[:：]?[年月日时分]*', text)):
            row.update(rectangle_field_kind='receiving_form_field' if index in value_rows else 'empty_receiving_field',
                       matching=False, zone_label='填写栏（不参与签章文字核对）')
            fields.append({'text': row['text'], 'image_url': row['image_url'],
                           'rectangle_row_index': row['rectangle_row_index'],
                           'caption': value_rows.get(index, (verified_empty or {}).get('label', text))})
    if fields:
        artifact['rectangular_form_fields'] = fields


# Compatibility for existing callers; filled cells now follow the same policy.
mark_empty_receiving_fields = mark_receiving_form_fields
