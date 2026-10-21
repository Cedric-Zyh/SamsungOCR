"""Image/OCR adapter for printed signature requirement recovery."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from PIL import Image

from ...domain.ocr import TextObservation
from ...domain.parsing import normalize_text
from ...providers.text import TextRecognizer, current_text_recognizer
from .requirement_image import requirement_crop_box, clean_and_deskew_requirement


def _recover_signature_requirement(
    source: str | Path,
    page_rows: list[TextObservation],
    primary: str,
    page_backend: str,
    customer: str = "",
    *,
    artifact_dir: str | Path | None = None,
    artifact_url_prefix: str = "",
    artifacts: list[dict] | None = None,
    text_recognizer: TextRecognizer | None = None,
) -> dict | None:
    """OCR the signature-requirement crop and return its text verbatim."""
    recognizer = text_recognizer if text_recognizer is not None else current_text_recognizer()
    if not recognizer.supports_line(page_backend):
        return None
    anchor = next(
        (
            row
            for row in page_rows
            if any(token in row.text for token in ("签章要求", "签幸要求", "签草要求"))
        ),
        None,
    )
    with Image.open(source) as image, tempfile.TemporaryDirectory(
        prefix="signature-line-"
    ) as temp_dir:
        box, boundary = requirement_crop_box(image.size, anchor, page_rows)
        crop = image.crop(box)
        path = Path(temp_dir) / "signature-requirement.jpg"
        color_clean, geometry = clean_and_deskew_requirement(crop)
        color_clean.save(path, quality=95)
        artifact = {}
        if artifact_dir and artifacts is not None:
            from shutil import copyfile

            directory = Path(artifact_dir) / "requirements"
            directory.mkdir(parents=True, exist_ok=True)
            crop.save(directory / "signature-requirement-original.png")
            # Keep the exact encoded input sent to OCR, not a later recreation.
            copyfile(path, directory / path.name)
            prefix = artifact_url_prefix.rstrip("/") + "/requirements"
            artifact = {
                "original_url": f"{prefix}/signature-requirement-original.png",
                "color_clean_url": f"{prefix}/{path.name}",
                "backend": page_backend,
                "ocr_text": "",
                "crop_box": list(box),
                "crop_boundary": boundary,
                **geometry,
            }
            artifacts.append(artifact)
        try:
            line_rows = recognizer.recognize_line(path, backend=page_backend)
        except Exception as exc:
            if artifact:
                artifact["error"] = str(exc)
            return None
    if not line_rows:
        return None
    best = max(line_rows, key=lambda row: row.confidence)
    raw = best.text.strip()
    if artifact:
        artifact.update(ocr_text=raw, confidence=round(float(best.confidence), 3))
    match = re.search(r"签[章幸草]要求\s*[:：]?\s*(.+)$", raw)
    candidate = match.group(1).strip() if match else ""
    # The cleaned crop is the requested signature-requirement OCR input. Keep
    # exactly what that OCR returned after removing only the printed field
    # label; do not repair, complete, trim, or replace any business text.
    if not candidate or len(normalize_text(candidate)) < 6:
        return None
    return {
        "value": candidate,
        "confidence": round(float(best.confidence), 3),
        "raw": raw,
    }
