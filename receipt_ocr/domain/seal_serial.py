"""Associate an independently read ring serial with its own stamp text."""
import re

from .parsing.parsing_seals import normalize_seal_text_strict


def serial_matching_texts(requirement, artifact):
    """Include serial evidence only when the requirement explicitly asks for it.

    Station numbers and short centre numbers are left to ordinary text matching.
    The suffix label is formatting; all company/type glyphs and serial digits
    must come from this physical stamp's observations.
    """
    expected = normalize_seal_text_strict(str(requirement or ""))
    suffix = re.fullmatch(r"(.*?)(印章编号|章号|编号)?[:：]?([0-9]{6,24})", expected)
    if not suffix or not suffix[1] or (not suffix[2] and suffix[1][-1].isdigit()):
        return []
    serial = str(artifact.get("ring_serial_text") or "")
    if (artifact.get("ring_serial_detection") or {}).get("status") != "recognized":
        return []
    if not re.fullmatch(r"[0-9]{6,24}", serial):
        return []
    ring = str(artifact.get("unwrapped_text") or "").strip()
    centre = str(artifact.get("round_type_band_text") or "").strip()
    if not ring:
        return []
    # Keep the requirement's separator/label, but never its numeric value.
    separator = expected[len(suffix[1]):-len(suffix[3])]
    return list(dict.fromkeys(text + separator + serial for text in
                             ([ring + centre, ring] if centre else [ring])))
