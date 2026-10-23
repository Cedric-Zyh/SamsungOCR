"""Keep empty receiving-form captions out of a verified rectangle's name."""
import re


def mark_empty_receiving_fields(artifact):
    if artifact.get('rectangular_layout') != 'frame_rows':
        return
    reads = [row for row in artifact.get('reads', []) if row.get('channel') == 'body']
    # A date stamp or an ordinary company name is not a receiving form. This
    # route needs its independently observed printed receiving-stamp purpose.
    if not any('收货专用章' in row.get('text', '') and row.get('confidence', 0) >= .75
               for row in reads):
        return
    fields = []
    for row in reads:
        text = re.sub(r'\s+', '', row.get('text', ''))
        index = row.get('rectangle_row_index', -1)
        prepared = artifact.get('rectangular_fixed_rows', [])
        verified_empty = prepared[index].get('empty_form_field') if 0 <= index < len(prepared) else None
        if verified_empty or re.fullmatch(r'(?:收货人|收货日期|收货时间|签收人|签收日期)[:：]?[年月日时分]*', text):
            row.update(rectangle_field_kind='empty_receiving_field', matching=False)
            fields.append({'text': row['text'], 'image_url': row['image_url'],
                           'rectangle_row_index': row['rectangle_row_index']})
    if fields:
        artifact['rectangular_form_fields'] = fields
