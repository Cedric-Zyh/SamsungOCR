"""Printed receipt field repairs and narrowly scoped OCR fallbacks."""

from __future__ import annotations
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from PIL import Image
from ..parsing import FIXED_FIELD_PHRASES, normalize_text
from ..ocr import TextObservation


def _is_neighboring_label_misread_as_receipt_note(value: str) -> bool:
    """Reject a nearby form label accidentally selected as the note value."""
    normalized = normalize_text(str(value))
    return normalized in {
        "实收数量",
        "拒收数量",
        "实收数量台",
        "拒收数量台",
        "收货客户",
        "仓库接收人",
        "盖章",
        "备注",
    }


def _recover_confirmed_template_note(rows: list[TextObservation], current: str) -> str:
    """Recover the user-confirmed fixed note when its printed label exists.

    The current Samsung receipt template always prints this exact sentence.
    OCR near the footer can return an unrelated short fragment (for example
    ``物流发``), so the label is the template anchor and ``current`` is kept
    only as immutable machine evidence by the caller.
    """
    del current
    if any(
        normalize_text(row.text).startswith(normalize_text("签收说明"))
        or any(
            token in normalize_text(row.text)
            for token in ("签章要求", "签幸要求", "签草要求")
        )
        for row in rows
    ):
        return FIXED_FIELD_PHRASES["签收说明"][0]
    return ""


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
) -> dict | None:
    """OCR the signature-requirement crop and return its text verbatim."""
    from ...providers.paddle_runtime import is_paddle_backend, variant_of
    if not is_paddle_backend(page_backend):
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
            from ...providers.paddle_runtime import recognize_line

            model_variant = variant_of(page_backend) or "mobile"
            line_rows = recognize_line(path, model_variant=model_variant)
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


def _prefer_detail_requirement(primary: str, detail: str) -> bool:
    """Select a fuller secondary requirement without accepting unrelated text."""
    from difflib import SequenceMatcher

    primary_key = "".join(str(primary).split())
    detail_key = "".join(str(detail).split())
    if len(detail_key) < 6:
        return False
    if len(primary_key) < 6:
        return True
    # ``维修专章`` is itself a complete printed stamp type on reviewed
    # Samsung receipts. A detail-page pass may hallucinate the extra ``用``
    # and look one glyph "fuller" than Paddle's exact reading. Keep the
    # primary form value in this one unambiguous insertion case; seal matching
    # handles ``维修专章``/``维修专用章`` as equivalent separately.
    if (
        primary_key.endswith("维修专章")
        and detail_key == primary_key.removesuffix("维修专章") + "维修专用章"
    ):
        return False
    similarity = SequenceMatcher(None, primary_key, detail_key).ratio()
    return similarity >= 0.72 and len(detail_key) >= len(primary_key) + 1


def _prefer_detail_field(primary: str, detail: str) -> bool:
    """Accept a fuller secondary business field only when both readings agree."""
    from difflib import SequenceMatcher

    primary_key = "".join(str(primary).split())
    detail_key = "".join(str(detail).split())
    if len(primary_key) < 6 or len(detail_key) <= len(primary_key):
        return False
    if len(detail_key) > len(primary_key) + 6:
        return False
    return SequenceMatcher(None, primary_key, detail_key).ratio() >= 0.82
