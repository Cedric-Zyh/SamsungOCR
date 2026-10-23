"""Publish requirement evidence from the existing page read, without more OCR."""
import re
from pathlib import Path

from PIL import Image

from ...domain.parsing import normalize_text
from .requirement_image import requirement_crop_box, _requirement_line_rows


def signature_requirement_from_page(source, rows, backend, *, clean_image,
                                    artifact_dir=None, artifact_url_prefix="", artifacts=None):
    anchor = next((row for row in rows
                   if re.match(r"^签[章幸草]要求", row.text.strip())), None)
    if anchor is None:
        return None
    line = _requirement_line_rows(clean_image.size, anchor, rows)
    # Use the very same verified baseline as the evidence crop. Re-parsing
    # all page rows can append a raised quantity unit, while the generic
    # parser's fixed right edge can drop a real station/phone at the far right.
    parts = [re.sub(r"^签[章幸草]要求\s*[:：]?\s*", "", anchor.text.strip())]
    parts.extend(row.text.strip() for row in line[1:])
    # Overlapping label/value boxes can both read the label's colon. Remove
    # only leading field separators; retain phone/business punctuation inside.
    value = re.sub(r"^(?:[:：]\s*)+", "", "".join(parts).strip())
    box, boundary = requirement_crop_box(clean_image.size, anchor, rows)
    raw = " ".join(row.text for row in line)
    confidence = min(float(row.confidence) for row in line)
    if artifact_dir and artifacts is not None:
        directory = Path(artifact_dir) / "requirements"
        directory.mkdir(parents=True, exist_ok=True)
        # Original and cleaned evidence share the original page coordinates.
        # No deskew or second contrast pass: this is the actual page input crop.
        from ...imaging.io import _read_image
        frame = _read_image(source)
        Image.fromarray(frame[:, :, ::-1]).crop(box).save(directory / "signature-requirement-original.png")
        clean_image.crop(box).save(directory / "signature-requirement.png")
        prefix = artifact_url_prefix.rstrip("/") + "/requirements"
        artifacts.append({
            "original_url": f"{prefix}/signature-requirement-original.png",
            "color_clean_url": f"{prefix}/signature-requirement.png",
            "backend": backend, "ocr_scope": "clean_page", "ocr_text": raw,
            "confidence": round(confidence, 3), "crop_box": list(box),
            "crop_boundary": boundary, "page_size": list(clean_image.size),
            "ocr_rows": [row.to_dict() for row in line],
        })
    if len(normalize_text(value)) < 6:
        return None
    return {"value": value, "confidence": round(confidence, 3), "raw": raw,
            "footer_row": anchor.to_dict()}
