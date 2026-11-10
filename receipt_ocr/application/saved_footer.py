"""Restore layout evidence from saved detections or saved full-page OCR only."""
from copy import deepcopy
import math

from ..domain.documents.layout import signature_footer_evidence
from ..domain.ocr import TextObservation


def saved_footer_evidence(*sources):
    """Accept saved contexts/results, preferring an explicit detection result.

    Legacy records can predate ``signature_footer`` while still retaining all
    OCR rows. Replay the same spatial detector used during recognition; dates,
    recognized stamps and extracted fields are never evidence of a footer.
    """
    sources = [source for source in sources if isinstance(source, dict)]
    for source in sources:
        for key in ('footer_detection', 'signature_footer'):
            footer = source.get(key)
            if isinstance(footer, dict) and isinstance(footer.get('present'), bool):
                return deepcopy(footer)
    insufficient = {}
    for source in sources:
        rows = []
        for saved in source.get('ocr_observations') or []:
            if not isinstance(saved, dict):
                continue
            try:
                row = TextObservation(**saved)
            except TypeError:
                continue
            if (not isinstance(row.text, str) or any(
                    not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in (row.confidence, row.x, row.y, row.width, row.height))):
                continue
            rows.append(row)
        if not rows:
            continue
        detected = signature_footer_evidence(rows)
        footer = {key: value for key, value in detected.items() if key != 'anchor'}
        if detected['anchor'] is not None:
            footer['anchor'] = detected['anchor'].to_dict()
        if footer['present']:
            return footer
        insufficient = footer
    return insufficient
