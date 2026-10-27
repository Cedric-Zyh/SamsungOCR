"""Use a stamp's verified circular reading order without rewriting its OCR."""
from .parsing.parsing_seals import normalize_seal_text_strict


def normalize_ring_candidates(artifact, texts):
    """Replace only observed ring strings that differ by their circular seam.

    Independent readings with different glyphs or counts remain conflicts.
    The raw reads and reorder metadata stay intact for inspection.
    """
    reorder = artifact.get('ring_reorder') or {}
    ordered = str(reorder.get('text') or '')
    normalized = normalize_seal_text_strict(ordered)
    if (not normalized or normalized != normalize_seal_text_strict(
            str(artifact.get('unwrapped_text') or ''))):
        return list(texts)
    equivalent = set()
    for read in artifact.get('reads') or []:
        if read.get('channel') != 'ring' or read.get('matching') is False:
            continue
        raw = normalize_seal_text_strict(str(read.get('text') or ''))
        if (raw != normalized and len(raw) == len(normalized)
                and normalized in raw + raw):
            equivalent.add(raw)
    return [ordered if normalize_seal_text_strict(text) in equivalent else text
            for text in texts]
