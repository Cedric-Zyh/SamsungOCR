"""Prepare shape-specific image variants before text recognition."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from receipt_ocr.imaging.contracts import SealRegion

from receipt_ocr.imaging.page import extract_region_text

from receipt_ocr.imaging.crops import save_color_isolated_seal, save_isolated_seal, save_rectangular_seal_code_line, save_region_crop

from receipt_ocr.imaging.ellipse import save_ellipse_normalized_seal

from receipt_ocr.imaging.bands import save_round_seal_type_band, round_seal_type_band_box, save_unwrapped_seal_bands

from receipt_ocr.imaging.unwrap import save_unwrapped_seal

from receipt_ocr.imaging.shapes import seal_region_is_rectangular, seal_region_is_elliptical
from receipt_ocr.providers.paddle_runtime import is_paddle_backend, variant_of
from receipt_ocr.recognition.seal.contracts import SealRequest, RegionEvidence
from receipt_ocr.recognition.seal.preprocess.shapes import classify_shape, prepared_from_evidence
from receipt_ocr.recognition.seal.preprocess.common import color_crop, crop_common


def _ring_input(evidence: RegionEvidence) -> tuple[Path | None, list[float] | None]:
    """Return the ring image and type-row box in one coordinate system."""
    oriented = evidence.color_isolated_oriented
    if oriented is not None:
        return oriented, evidence.orientation.get("oriented_type_row_box")
    return evidence.color_isolated, evidence.orientation.get("type_row_box")


def _collect_primary_region_evidence(
    request: SealRequest,
    evidence: RegionEvidence,
    index: int,
    region: SealRegion,
    temp_dir: str,
) -> None:
    """Crop, orient and prepare variants for one region."""
    evidence.region_texts: list[str] = []
    evidence.shape = classify_shape(request.source, region)
    evidence.rectangular = evidence.shape == "rectangle"
    evidence.elliptical = evidence.shape == "ellipse"
    evidence.original = Path(temp_dir) / f"seal-{index}-original.jpg"
    crop_common(request.source, region, evidence.original)
    # Page text can contain the printed seal requirement; keep it out of matching.
    evidence.whole_text = extract_region_text(request.rows, region)
    evidence.isolated = Path(temp_dir) / f"seal-{index}-isolated.png"
    save_isolated_seal(request.source, evidence.isolated, region)
    evidence.color_isolated = Path(temp_dir) / f"seal-{index}-color-isolated.png"
    color_crop(request.source, region, evidence.color_isolated)
    evidence.color_isolated_oriented = None
    evidence.ellipse_normalized = None
    evidence.orientation = {}
    evidence.orientation_anchor_text = ""
    # A round stamp's outer circle has no direction.  The configured strategy
    # either keeps the crop unchanged, uses a detected ``用章``/``专用章``
    # polygon as the direction anchor, or applies Paddle's four-way document
    # orientation classifier.  Only the color-safe derivative is rotated.
    if (
        request.orientation_mode != "none"
        and (not evidence.rectangular or request.orientation_mode in {"doc_ori", "combined"})
    ):
        try:
            from receipt_ocr.recognition.seal.preprocess.ellipse import prepare_ellipse_stamp
            from receipt_ocr.recognition.seal.preprocess.round import (
                prepare_round_stamp,
                prepare_round_stamp_doc_ori,
                prepare_round_stamp_combined,
            )

            oriented_path = Path(temp_dir) / f"seal-{index}-color-isolated-oriented.png"
            if request.orientation_mode == "doc_ori":
                oriented, evidence.orientation = prepare_round_stamp_doc_ori(
                    evidence.color_isolated, oriented_path
                )
            elif evidence.elliptical:
                oriented, evidence.orientation = prepare_ellipse_stamp(
                    evidence.color_isolated,
                    oriented_path,
                    model_variant=variant_of(request.ocr_backend) or "v6",
                )
            elif request.orientation_mode == "combined":
                oriented, evidence.orientation = prepare_round_stamp_combined(
                    evidence.color_isolated,
                    oriented_path,
                    model_variant=variant_of(request.ocr_backend) or "v6",
                )
            else:
                oriented, evidence.orientation = prepare_round_stamp(
                    evidence.color_isolated,
                    oriented_path,
                    model_variant=variant_of(request.ocr_backend) or "v6",
                )
            if oriented is not None and oriented.is_file():
                evidence.color_isolated_oriented = oriented
            evidence.orientation_anchor_text = str(
                evidence.orientation.get("anchor_text", "")
            ).strip()
        except Exception as exc:
            evidence.orientation = {
                "mode": request.orientation_mode,
                "status": "印章方向检测失败，保留原方向",
                "error": str(exc),
                "applied_rotation": 0.0,
            }
    elif request.orientation_mode == "none":
        evidence.orientation = {
            "mode": "none",
            "status": "按设置保留印章原方向",
            "angle": None,
            "applied_rotation": 0.0,
            "confidence": 1.0,
        }
    # An oval must be normalized before any type-band or ring OCR.  Keep this
    # image visible as a first-class artifact so the subsequent stages can be
    # evidenceed instead of silently stretching inside the polar transform.
    if evidence.elliptical:
        try:
            ellipse_source = evidence.color_isolated_oriented or evidence.color_isolated
            evidence.ellipse_normalized = Path(temp_dir) / f"seal-{index}-ellipse-normalized.png"
            save_ellipse_normalized_seal(
                ellipse_source,
                evidence.ellipse_normalized,
                color=region.color,
            )
            # ``ellipse_source`` is the final path returned by the one
            # orientation pass. The normalized image, type band, unwrap and
            # all following OCR deliberately consume this derivative rather
            # than falling back to the raw color crop.
            evidence.orientation["ellipse_preprocess_source"] = str(ellipse_source)
            evidence.orientation["ellipse_ocr_source"] = str(
                evidence.ellipse_normalized
            )
        except Exception:
            evidence.ellipse_normalized = None
    evidence.round_type_band = None
    evidence.round_type_band_texts = []
    type_band_focus_box = None
    # Keep the horizontal stamp-type row as a first-class v6 input. The
    # circular company name remains read from the polar-unwrapped image below;
    # this band prevents the two geometries from competing in one detector.
    if not evidence.rectangular:
        try:
            evidence.round_type_band = Path(temp_dir) / f"seal-{index}-round-type-band.png"
            # The fixed band is meaningful only after the round stamp has
            # been put into its detected text orientation.  Otherwise the
            # lower slice can contain an arbitrary arc of the company name.
            if evidence.elliptical and evidence.ellipse_normalized is not None:
                # The normalized oval is square and upright by construction;
                # do not reuse a pre-normalization polygon box, which can
                # point at the lower arc and yield the partial crop seen in
                # the review page.
                band_source = evidence.ellipse_normalized
                type_band_focus_box = None
                band_aligned = True
            else:
                band_source = evidence.color_isolated_oriented or evidence.color_isolated
                type_band_focus_box = evidence.orientation.get("oriented_type_row_box")
                band_aligned = evidence.color_isolated_oriented is not None
            if type_band_focus_box is None and not band_aligned and request.orientation_mode == "none":
                type_band_focus_box = round_seal_type_band_box(
                    band_source, orientation_aligned=False
                )
            save_round_seal_type_band(
                band_source,
                evidence.round_type_band,
                orientation_aligned=band_aligned,
                focus_box=type_band_focus_box,
            )
            # Text recognition happens in ``ocr.region`` after all variants are ready.
        except Exception:
            evidence.round_type_band = None
            evidence.round_type_band_texts = []
    evidence.code_line = None
    evidence.code_line_texts: list[str] = []
    if evidence.rectangular and re.search(r"\d{6,12}", request.requirement):
        try:
            evidence.code_line = Path(temp_dir) / f"seal-{index}-code-line.png"
            save_rectangular_seal_code_line(request.source, evidence.code_line, region)
        except Exception:
            evidence.code_line = None
    evidence.crop_text = ""
    evidence.color_isolated_texts = []
    evidence.oriented_texts = []
    ring_exclude_boxes = []
    # The ring derivative must use the same image coordinate system as the
    # detected box. When orientation produced a rotated crop, its
    # ``oriented_type_row_box`` is the usable box; the source-space box belongs
    # to the unrotated color crop. Previously the unwrap was rebuilt from the
    # page image, so this evidence was silently discarded.
    ring_source, source_type_box = _ring_input(evidence)
    if source_type_box is None and type_band_focus_box is not None:
        source_type_box = type_band_focus_box
    if source_type_box is not None:
        try:
            with Image.open(ring_source) as image:
                mask_width, mask_height = image.size
            left, top, right, bottom = [float(value) for value in source_type_box]
            box_width = max(0.0, right - left)
            box_height = max(0.0, bottom - top)
            # A ring mask may only erase one horizontal text row.  After a
            # coarse quarter-turn the type row sits diagonally in the source
            # crop, so mapping its tight oriented box back produces a large,
            # roughly square bounding box (measured 527x845 on a 1000x1022
            # crop).  Masking that removes a diagonal slice of the circular
            # company name instead -- it cost ``京凌`` on 7266220440.jpg.
            # Requiring a row-like aspect keeps a slightly tilted row maskable
            # while a diagonal one is left in the 展开图, which reads correctly.
            row_like = box_height <= 0 or box_width / box_height >= 1.8
            # A diagonal detector polygon can cover most of the circular crop
            # even though it represents only the centre ``收货专用章`` text.
            # Do not use such a broad box to erase the ring before unwrapping;
            # that was the reason some otherwise clear red ring lettering
            # produced a nearly blank black/white展开图.
            if row_like and (
                box_width <= mask_width * 0.65
                or box_height <= mask_height * 0.45
            ):
                ring_exclude_boxes.append(
                    (
                        max(0.0, left / mask_width),
                        max(0.0, top / mask_height),
                        min(1.0, right / mask_width),
                        min(1.0, bottom / mask_height),
                    )
                )
                evidence.orientation["ring_type_mask_box"] = ring_exclude_boxes[-1]
            else:
                evidence.orientation["ring_type_mask_box"] = None
                evidence.orientation["ring_type_mask_skipped"] = (
                    "章型检测框非单行（斜置）或覆盖过大，保留环形文字展开区域"
                    if not row_like
                    else "章型检测框覆盖过大，保留环形文字展开区域"
                )
        except (OSError, TypeError, ValueError, ZeroDivisionError):
            ring_exclude_boxes = []
    evidence.unwrapped = Path(temp_dir) / f"seal-{index}-unwrapped.png"
    if evidence.elliptical and evidence.ellipse_normalized is not None:
        # The ellipse is stretched first; every later ring stage consumes this
        # square image, never the original oval crop.
        save_unwrapped_seal(
            evidence.ellipse_normalized,
            evidence.unwrapped,
            None,
            elliptical=True,
            normalized=True,
            color=region.color,
        )
        evidence.orientation["ellipse_route"] = "先椭圆拉伸校正，再进入横向分带和环形展开"
    else:
        try:
            save_unwrapped_seal(
                ring_source,
                evidence.unwrapped,
                None,
                exclude_boxes=ring_exclude_boxes,
                elliptical=evidence.elliptical,
                color=region.color,
            )
        except TypeError:
            # Keep lightweight crop adapters compatible with the common
            # three-argument image writer contract.
            save_unwrapped_seal(ring_source, evidence.unwrapped, None)
    evidence.unwrapped_rotated = (
        Path(temp_dir) / f"seal-{index}-unwrapped-rotated-180.png"
    )
    evidence.color_isolated_rotations = (
        Path(temp_dir) / f"seal-{index}-color-isolated-rotations.png"
    )
    if not evidence.rectangular and request.orientation_mode != "none":
        # Company lettering and the stamp-type suffix on a round
        # seal can face opposite directions after polar unwrap.
        # Keep the 180-degree derivative visible and reserve it
        # for the color-only Secondary evidence below.  No neutral form
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
        # row.  Build one evidenceable contact sheet from the same
        # color-only crop at 90/180/270 degrees.  The Secondary model
        # reads it in a single call, so coverage improves without
        # tripling batch latency or admitting black form text.
        try:
            rotation_source = evidence.ellipse_normalized or evidence.color_isolated
            with Image.open(rotation_source) as image:
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
    evidence.unwrap_texts = []
    if not evidence.rectangular:
        try:
            evidence.unwrapped_bands = list(save_unwrapped_seal_bands(
                evidence.unwrapped,
                evidence.unwrapped.with_name(f"{evidence.unwrapped.stem}-band"),
            ))
        except Exception:
            evidence.unwrapped_bands = []

    # Rectangular service-center stamps are occasionally applied
    # upside down. Rotate the already color-isolated image, not
    # the original crop, so black form text can never become
    # circular matching evidence.
    evidence.rotated = Path(temp_dir) / f"seal-{index}-rotated-180.png"
    evidence.rotated_texts: list[str] = []
    if (evidence.rectangular and request.orientation_mode == "polygon"
            and index not in request.orientation_resolved_indices):
        try:
            with Image.open(evidence.isolated) as image:
                image.rotate(180, expand=False).save(evidence.rotated)
            evidence.rotated_texts = []
        except Exception:
            evidence.rotated_texts = []

    evidence.prepared = prepared_from_evidence(index, evidence.shape, evidence)


def _collect_secondary_region_evidence(
    request: SealRequest,
    evidence: RegionEvidence,
    region: SealRegion,
) -> None:
    """Prepare metadata for optional additional OCR without reading text."""
    evidence.secondary_original_texts = []
    evidence.secondary_color_isolated_texts = []
    evidence.secondary_crop_texts = []
    evidence.secondary_unwrap_texts = []
    evidence.secondary_rotated_texts = []
    evidence.secondary_code_line_texts = []
    evidence.original_safe_for_matching = bool(
        request.footer_anchor_y is not None and region.y >= request.footer_anchor_y + 0.075
    )
