"""Regular local seal crops and independent primary/secondary OCR evidence."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from .image_processing import (
    SealRegion,
    extract_region_text,
    save_color_isolated_seal,
    save_isolated_seal,
    save_rectangular_seal_code_line,
    save_region_crop,
    save_unwrapped_seal,
    seal_region_is_rectangular,
)
from .ocr_backends import backend_label, recognize_text
from .recognition_utils import _dedupe
from .seal_rules import (
    _reconstruct_exact_company_stamp_from_region,
    combine_region_texts,
)
from .seal_crop_types import SealCropRequest, SealEvidenceCollection, SealRegionEvidence


def _collect_primary_region_evidence(
    request: SealCropRequest,
    evidence: SealRegionEvidence,
    index: int,
    region: SealRegion,
    temp_dir: str,
) -> None:
    """Read the primary color-safe transforms in their original order."""
    evidence.region_texts: list[str] = []
    evidence.rectangular = seal_region_is_rectangular(request.source, region)
    evidence.original = Path(temp_dir) / f"seal-{index}-original.jpg"
    save_region_crop(request.source, evidence.original, region)
    evidence.whole_text = extract_region_text(request.rows, region)
    if evidence.whole_text:
        evidence.region_texts.append(evidence.whole_text)
    evidence.isolated = Path(temp_dir) / f"seal-{index}-isolated.png"
    save_isolated_seal(request.source, evidence.isolated, region)
    evidence.color_isolated = Path(temp_dir) / f"seal-{index}-color-isolated.png"
    save_color_isolated_seal(request.source, evidence.color_isolated, region)
    evidence.code_line = None
    evidence.code_line_texts: list[str] = []
    if evidence.rectangular and re.search(r"\d{6,12}", request.requirement):
        try:
            evidence.code_line = Path(temp_dir) / f"seal-{index}-code-line.png"
            save_rectangular_seal_code_line(request.source, evidence.code_line, region)
        except Exception:
            evidence.code_line = None
        try:
            if evidence.code_line is None or not evidence.code_line.is_file():
                raise ValueError("矩形编号章数字行未生成")
            if request.ocr_backend in {"paddle", "paddle_server"}:
                from .paddle_ocr import recognize_line

                code_rows = recognize_line(
                    evidence.code_line,
                    model_variant=(
                        "server" if request.ocr_backend == "paddle_server" else "mobile"
                    ),
                )
            else:
                code_rows = recognize_text(
                    evidence.code_line,
                    backend=request.ocr_backend,
                    min_text_height=0.025,
                )
            evidence.code_line_texts = [row.text for row in code_rows if row.text]
            evidence.region_texts.extend(evidence.code_line_texts)
        except Exception:
            evidence.code_line_texts = []
    crop_rows = []
    try:
        crop_rows = recognize_text(
            evidence.isolated, backend=request.ocr_backend, min_text_height=0.012
        )
    except Exception:
        pass
    evidence.crop_text = "".join(row.text for row in crop_rows)
    if evidence.crop_text:
        evidence.region_texts.append(evidence.crop_text)
    color_isolated_rows = []
    try:
        color_isolated_rows = recognize_text(
            evidence.color_isolated,
            backend=request.ocr_backend,
            min_text_height=0.012,
        )
    except Exception:
        pass
    evidence.color_isolated_texts = [row.text for row in color_isolated_rows if row.text]
    evidence.region_texts.extend(evidence.color_isolated_texts)
    evidence.unwrapped = Path(temp_dir) / f"seal-{index}-unwrapped.png"
    save_unwrapped_seal(request.source, evidence.unwrapped, region)
    evidence.unwrapped_rotated = (
        Path(temp_dir) / f"seal-{index}-unwrapped-rotated-180.png"
    )
    evidence.color_isolated_rotations = (
        Path(temp_dir) / f"seal-{index}-color-isolated-rotations.png"
    )
    if not evidence.rectangular:
        # Company lettering and the stamp-type suffix on a round
        # seal can face opposite directions after polar unwrap.
        # Keep the 180-degree derivative visible and reserve it
        # for the color-only Server audit below.  No neutral form
        # text can enter this image.
        try:
            with Image.open(evidence.unwrapped) as image:
                image.rotate(180, expand=False).save(evidence.unwrapped_rotated)
        except Exception:
            evidence.unwrapped_rotated = None
        # The legal company name normally follows the circular
        # border, but the stamp-type row (for example
        # ``业务专用章``) is often printed across the centre at an
        # arbitrary angle.  Polar unwrapping therefore preserves
        # the company while bending or dropping this independent
        # row.  Build one auditable contact sheet from the same
        # color-only crop at 90/180/270 degrees.  The Server model
        # reads it in a single call, so coverage improves without
        # tripling batch latency or admitting black form text.
        try:
            with Image.open(evidence.color_isolated) as image:
                base = image.convert("RGB")
                rotations = [
                    base.rotate(angle, expand=True, fillcolor="white")
                    for angle in (90, 180, 270)
                ]
                gap = 24
                sheet_width = max(item.width for item in rotations)
                sheet_height = sum(item.height for item in rotations) + gap * (
                    len(rotations) - 1
                )
                contact_sheet = Image.new(
                    "RGB", (sheet_width, sheet_height), "white"
                )
                cursor_y = 0
                for item in rotations:
                    contact_sheet.paste(
                        item,
                        ((sheet_width - item.width) // 2, cursor_y),
                    )
                    cursor_y += item.height + gap
                contact_sheet.save(evidence.color_isolated_rotations)
        except Exception:
            evidence.color_isolated_rotations = None
    else:
        evidence.unwrapped_rotated = None
        evidence.color_isolated_rotations = None
    unwrap_rows = []
    try:
        unwrap_rows = recognize_text(
            evidence.unwrapped, backend=request.ocr_backend, min_text_height=0.05
        )
    except Exception:
        pass
    evidence.unwrap_texts = [row.text for row in unwrap_rows if row.text]
    evidence.region_texts.extend(evidence.unwrap_texts)

    # Rectangular service-center stamps are occasionally applied
    # upside down. Rotate the already color-isolated image, not
    # the original crop, so black form text can never become
    # circular matching evidence.
    evidence.rotated = Path(temp_dir) / f"seal-{index}-rotated-180.png"
    evidence.rotated_texts: list[str] = []
    if evidence.rectangular:
        try:
            with Image.open(evidence.isolated) as image:
                image.rotate(180, expand=False).save(evidence.rotated)
            evidence.rotated_texts = [
                row.text
                for row in recognize_text(
                    evidence.rotated,
                    backend=request.ocr_backend,
                    min_text_height=0.012,
                )
                if row.text
            ]
        except Exception:
            evidence.rotated_texts = []
        evidence.region_texts.extend(evidence.rotated_texts)


def _collect_secondary_region_evidence(
    request: SealCropRequest,
    evidence: SealRegionEvidence,
    region: SealRegion,
) -> None:
    """Read independent secondary transforms and retain the original-crop guard."""
    evidence.secondary_original_texts: list[str] = []
    evidence.secondary_color_isolated_texts: list[str] = []
    evidence.secondary_crop_texts: list[str] = []
    evidence.secondary_unwrap_texts: list[str] = []
    evidence.secondary_rotated_texts: list[str] = []
    evidence.secondary_code_line_texts: list[str] = []
    evidence.original_safe_for_matching = False
    if request.secondary_ocr_backend:
        # Circular seals frequently split the company name and the
        # stamp-type suffix across different transforms.  In
        # Hybrid mode retain Paddle and Vision as independent OCR
        # evidence, then combine only text actually recognized.
        try:
            evidence.secondary_original_texts = [
                row.text
                for row in recognize_text(
                    evidence.original,
                    backend=request.secondary_ocr_backend,
                    min_text_height=0.012,
                )
                if row.text
            ]
        except Exception:
            pass
        if evidence.rectangular and evidence.rotated.is_file():
            try:
                evidence.secondary_rotated_texts = [
                    row.text
                    for row in recognize_text(
                        evidence.rotated,
                        backend=request.secondary_ocr_backend,
                        min_text_height=0.012,
                    )
                    if row.text
                ]
            except Exception:
                pass
        try:
            evidence.secondary_color_isolated_texts = [
                row.text
                for row in recognize_text(
                    evidence.color_isolated,
                    backend=request.secondary_ocr_backend,
                    min_text_height=0.012,
                )
                if row.text
            ]
        except Exception:
            pass
        try:
            evidence.secondary_crop_texts = [
                row.text
                for row in recognize_text(
                    evidence.isolated,
                    backend=request.secondary_ocr_backend,
                    min_text_height=0.012,
                )
                if row.text
            ]
        except Exception:
            pass
        try:
            evidence.secondary_unwrap_texts = [
                row.text
                for row in recognize_text(
                    evidence.unwrapped,
                    backend=request.secondary_ocr_backend,
                    min_text_height=0.04,
                )
                if row.text
            ]
        except Exception:
            pass
        if evidence.code_line is not None and evidence.code_line.is_file():
            try:
                if request.secondary_ocr_backend in {
                    "paddle",
                    "paddle_server",
                }:
                    from .paddle_ocr import recognize_line

                    secondary_code_rows = recognize_line(
                        evidence.code_line,
                        model_variant=(
                            "server"
                            if request.secondary_ocr_backend == "paddle_server"
                            else "mobile"
                        ),
                    )
                else:
                    secondary_code_rows = recognize_text(
                        evidence.code_line,
                        backend=request.secondary_ocr_backend,
                        min_text_height=0.025,
                    )
                evidence.secondary_code_line_texts = [
                    row.text for row in secondary_code_rows if row.text
                ]
            except Exception:
                pass
        # Keep original-color crop OCR in the audit artifact, but
        # do not use it as stamp-matching evidence. It contains
        # black printed form text whenever a red stamp overlaps
        # the requirement/note rows, which can otherwise create
        # a circular 100% match against the printed requirement.
        # A crop wholly below the footer label cannot contain the
        # printed requirement, however, and is valuable for pale
        # duplicate/customer stamps whose color mask drops thin
        # company strokes.  Keep the positional guard explicit.
        evidence.original_safe_for_matching = bool(
            request.footer_anchor_y is not None and region.y >= request.footer_anchor_y + 0.075
        )
        if evidence.original_safe_for_matching:
            evidence.region_texts.extend(evidence.secondary_original_texts)
        evidence.region_texts.extend(evidence.secondary_crop_texts)
        evidence.region_texts.extend(evidence.secondary_color_isolated_texts)
        evidence.region_texts.extend(evidence.secondary_unwrap_texts)
        evidence.region_texts.extend(evidence.secondary_rotated_texts)
        evidence.region_texts.extend(evidence.secondary_code_line_texts)


def _record_region_evidence(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    evidence: SealRegionEvidence,
    index: int,
    region: SealRegion,
) -> None:
    """Record regional text, artifacts and candidates for the later audit."""
    evidence.same_region_reconstructed_text = (
        _reconstruct_exact_company_stamp_from_region(request.requirement, evidence.region_texts)
    )
    if evidence.same_region_reconstructed_text:
        evidence.region_texts.append(evidence.same_region_reconstructed_text)
    evidence.combined_text = combine_region_texts(evidence.region_texts)
    collection.texts.extend(evidence.region_texts)
    if len(evidence.region_texts) >= 2 and evidence.combined_text:
        collection.texts.append(evidence.combined_text)
    if request.artifact_dir:
        prefix = request.artifact_url_prefix.rstrip("/")
        collection.artifacts.append(
            {
                "index": index,
                "color": region.color,
                "role": region.role,
                "shape": "矩形" if evidence.rectangular else "圆形",
                "ocr_backend": backend_label(request.ocr_backend),
                "secondary_ocr_backend": (
                    backend_label(request.secondary_ocr_backend)
                    if request.secondary_ocr_backend
                    else ""
                ),
                "original_url": f"{prefix}/seals/{evidence.original.name}",
                "isolated_url": f"{prefix}/seals/{evidence.isolated.name}",
                "color_isolated_url": f"{prefix}/seals/{evidence.color_isolated.name}",
                "code_line_url": (
                    f"{prefix}/seals/{evidence.code_line.name}"
                    if evidence.code_line is not None and evidence.code_line.is_file()
                    else ""
                ),
                "unwrapped_url": f"{prefix}/seals/{evidence.unwrapped.name}",
                "unwrapped_rotated_url": (
                    f"{prefix}/seals/{evidence.unwrapped_rotated.name}"
                    if evidence.unwrapped_rotated is not None
                    and evidence.unwrapped_rotated.is_file()
                    else ""
                ),
                "color_isolated_rotations_url": (
                    f"{prefix}/seals/{evidence.color_isolated_rotations.name}"
                    if evidence.color_isolated_rotations is not None
                    and evidence.color_isolated_rotations.is_file()
                    else ""
                ),
                "rotated_url": (
                    f"{prefix}/seals/{evidence.rotated.name}"
                    if evidence.rectangular and evidence.rotated.is_file()
                    else ""
                ),
                "page_text": evidence.whole_text,
                "isolated_text": evidence.crop_text,
                "color_isolated_text": " | ".join(evidence.color_isolated_texts),
                "code_line_text": " | ".join(evidence.code_line_texts),
                "unwrapped_text": " | ".join(evidence.unwrap_texts),
                "rotated_text": " | ".join(evidence.rotated_texts),
                "secondary_original_text": " | ".join(evidence.secondary_original_texts),
                "secondary_original_used_for_matching": evidence.original_safe_for_matching,
                "secondary_isolated_text": " | ".join(evidence.secondary_crop_texts),
                "secondary_color_isolated_text": " | ".join(
                    evidence.secondary_color_isolated_texts
                ),
                "secondary_unwrapped_text": " | ".join(evidence.secondary_unwrap_texts),
                "secondary_rotated_text": " | ".join(evidence.secondary_rotated_texts),
                "secondary_code_line_text": " | ".join(
                    evidence.secondary_code_line_texts
                ),
                "same_region_reconstructed_text": (
                    evidence.same_region_reconstructed_text
                ),
                "combined_text": evidence.combined_text,
            }
        )
    collection.server_candidates.append(
        {
            "index": index,
            "region": region,
            "pixel_ratio": float(region.pixel_ratio),
            "isolated": evidence.isolated,
            "color_isolated": evidence.color_isolated,
            "code_line": evidence.code_line,
            "unwrapped": evidence.unwrapped,
            "unwrapped_rotated": evidence.unwrapped_rotated,
            "color_isolated_rotations": evidence.color_isolated_rotations,
            "rotated": evidence.rotated if evidence.rectangular and evidence.rotated.is_file() else None,
            "rectangular": evidence.rectangular,
            "evidence": _dedupe(
                evidence.region_texts + ([evidence.combined_text] if evidence.combined_text else [])
            ),
        }
    )
