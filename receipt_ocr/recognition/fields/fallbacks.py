"""Image/OCR adapter for printed signature requirement recovery."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from PIL import Image

from ...domain.ocr import TextObservation
from ...domain.parsing import normalize_text
from ...providers.text import TextRecognizer, default_text_recognizer


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
    recognizer = text_recognizer or default_text_recognizer()
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
    anchor_y = anchor.y if anchor else 0.468
    with Image.open(source) as image, tempfile.TemporaryDirectory(
        prefix="signature-line-"
    ) as temp_dir:
        width, height = image.size
        y1 = max(0, round((anchor_y - 0.004) * height))
        # Keep the retry crop on the signature-requirement row itself.  The
        # previous fixed downward extension also captured the next footer row
        # (for example ``实收数量``), allowing its first glyph to be appended
        # to the requirement value.
        anchor_bottom = anchor_y + (anchor.height if anchor else 0.013)
        y2 = min(height, round((anchor_bottom + 0.002) * height))
        crop = image.crop((round(0.015 * width), y1, round(0.64 * width), y2))
        path = Path(temp_dir) / "signature-requirement.jpg"
        # The requested preprocessing for this region is color suppression;
        # the OCR text produced from this input is returned without semantic
        # correction. Seal OCR continues to use the original color image.
        from PIL import ImageChops, ImageOps

        red, green, blue = crop.convert("RGB").split()
        color_clean = ImageChops.lighter(red, ImageChops.lighter(green, blue))
        color_clean = ImageOps.autocontrast(color_clean, cutoff=1)
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
