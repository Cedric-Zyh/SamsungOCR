"""Printed receipt field repairs and narrowly scoped OCR fallbacks."""

from __future__ import annotations
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from PIL import Image
from .parser import FIXED_FIELD_PHRASES, normalize_text
from .ocr_types import TextObservation


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
    """Retry the fixed signature-requirement row without text detection."""
    from .paddle_ocr import is_paddle_backend, variant_of
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
        # The printed requirement line is often crossed by a red receiving
        # stamp.  OCR on the raw line then treats red stamp glyphs as extra
        # black text (for example, inserting a second company name between
        # the city and the customer).  Keep the same geometry, but recognize
        # the max RGB channel: neutral black print stays dark while red ink
        # becomes light and drops out of this field-only pass.  Seal OCR still
        # uses the original color image elsewhere in the pipeline.
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
            from .paddle_ocr import recognize_line

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
    candidate = _trim_signature_requirement_candidate(candidate)
    if not candidate or len(normalize_text(candidate)) < 6:
        return None
    # The red-suppressed line can recover the printed company almost fully,
    # while dropping one character that is covered by the stamp (``口`` is
    # commonly lost in ``旅顺口区``).  When the independently printed customer
    # field agrees strongly and the fixed stamp suffix is intact, restore only
    # that company portion from the customer field.  This remains independent
    # of the seal OCR result and does not copy text from the actual stamp.
    customer_key = normalize_text(customer)
    candidate_key = normalize_text(candidate)
    customer_repaired = False
    for suffix in ("售后服务专用章", "售后专用章", "仓储部收货章"):
        if not customer_key or not candidate_key.endswith(suffix):
            continue
        observed_company = candidate_key[: -len(suffix)]
        company_similarity = SequenceMatcher(None, observed_company, customer_key).ratio()
        if observed_company != customer_key and company_similarity >= 0.88:
            candidate = f"{customer}{suffix}"
            candidate_key = normalize_text(candidate)
            customer_repaired = True
            break
    primary_key = normalize_text(primary)
    if len(primary_key) >= 6:
        # A primary value ending in a known numbered service-center structure
        # is already complete business evidence. Recognition-only retries can
        # easily append characters from the neighboring quantity cells (for
        # example ``筑业业兴``) while remaining superficially similar. Do not
        # replace the structured primary merely because the noisy retry is
        # longer.
        if re.search(r"(?:维修中心|售后)\d{6,10}(?:站)?$", primary_key):
            return None
        # The full-page detector can preserve the stable Samsung
        # ``...服务中`` grammar while losing only the final ``心``.  The
        # contextual repair below has an intentionally narrow rule for that
        # one-glyph loss.  A wider recognition-only crop sometimes drops an
        # earlier glyph and appends fragments of the neighbouring fixed note
        # (for example ``三电子孝感服务中单整收``); being longer must not make
        # that candidate preferable to the structurally safer primary value.
        if re.fullmatch(r"三星电子[\u4e00-\u9fff（）()]{2,16}服务中", primary_key):
            return None
        similarity = SequenceMatcher(None, primary_key, candidate_key).ratio()
        if similarity < 0.72 or (
            len(candidate_key) <= len(primary_key) and not customer_repaired
        ):
            return None
        if customer_key:
            primary_customer = SequenceMatcher(None, primary_key, customer_key).ratio()
            candidate_customer = SequenceMatcher(
                None, candidate_key, customer_key
            ).ratio()
            # When the requirement is recognizably the customer entity, a
            # retry must not replace it with a longer but less accurate line.
            if (
                max(primary_customer, candidate_customer) >= 0.55
                and candidate_customer < primary_customer
            ):
                return None
    return {
        "value": candidate,
        "confidence": round(float(best.confidence), 3),
        "raw": raw,
    }


def _trim_signature_requirement_candidate(value: str) -> str:
    """Remove right-hand quantity cells accidentally joined to the field."""
    candidate = re.split(r"实?收数(?:量)?|拒收数(?:量)?|签收说明", value, maxsplit=1)[0]
    candidate = re.sub(r"(?:整|数)?签收$", "", candidate)
    # In the fixed receipt layout the next right-hand heading is ``实收数量``.
    # A narrow OCR row can retain only its first glyph and append it to an
    # otherwise complete service-centre requirement.  Limit this cleanup to
    # the exact business suffix so genuine names ending in “收” are not
    # trimmed generically.
    candidate = re.sub(r"(服务中心)收$", r"\1", candidate)
    # A contact-name requirement may end in a mobile number.  The following
    # fixed note begins with “视为整单…”, and OCR can append only “为整” to
    # that number.  Require the full phone-shaped suffix before removing it;
    # arbitrary organization text ending in the same characters is retained.
    candidate = re.sub(r"(\d{7,11})为整(?:单)?(?:完)?$", r"\1", candidate)
    phone = re.search(r"电话\s*[:：]?\s*[0-9\-]{7,}", candidate)
    if phone:
        candidate = candidate[: phone.end()]
    elif re.search(r"(?:收货章|专用章)", candidate):
        # Some legitimate requirement identifiers follow the stamp type:
        # ``检测专用章（3）`` and ``维修专用章045153608531``.  The old boundary
        # cleanup stopped exactly at ``专用章`` and silently discarded these
        # business-significant suffixes.  Preserve only a tightly structured
        # parenthesized index and/or 6-12 digit identifier; arbitrary adjacent
        # quantity-cell noise is still cut away.
        end = max(
            match.end()
            for match in re.finditer(
                r"(?:收货章|专用章)"
                r"(?:\s*[（(]\d{1,3}[）)])?"
                r"(?:\s*\d{6,12}(?:站)?)?",
                candidate,
            )
        )
        candidate = candidate[:end]
    else:
        numbered_center = re.search(r"维修中心\s*\d{6,10}", candidate)
        if numbered_center:
            candidate = candidate[: numbered_center.end()]
        else:
            # One reviewed pickup requirement continues after the station id
            # with an independently printed 11-digit phone number and legal
            # company name. Preserve that complete grammar before applying
            # the ordinary ``站代码 + id`` boundary.
            station_contact = re.search(
                r"站代码\s*[:：]?\s*\d{6,10}\s*"
                r"\d{10,12}\s*[\u4e00-\u9fff]{4,40}有限公司",
                candidate,
            )
            if station_contact:
                candidate = candidate[: station_contact.end()]
            else:
                authorization_code = re.search(
                    r"(?:授权代码|站代码)\s*[:：]?\s*\d{6,10}",
                    candidate,
                )
                if authorization_code:
                    candidate = candidate[: authorization_code.end()]
    return candidate.rstrip("：:；;，,。 ")


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
