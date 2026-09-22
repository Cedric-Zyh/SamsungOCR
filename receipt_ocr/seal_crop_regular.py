"""Regular local seal crops and independent primary/secondary OCR evidence."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from .image_processing import (
    SealRegion,
    extract_region_text,
    save_color_isolated_seal,
    save_ellipse_normalized_seal,
    save_isolated_seal,
    save_rectangular_seal_code_line,
    save_region_crop,
    save_round_seal_type_band,
    round_seal_type_band_box,
    save_unwrapped_seal,
    save_unwrapped_seal_bands,
    seal_region_is_rectangular,
    seal_region_is_elliptical,
)
from .ocr_backends import backend_label, recognize_text
from .paddle_ocr import is_paddle_backend, variant_of
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
    evidence.elliptical = (
        not evidence.rectangular
        and seal_region_is_elliptical(request.source, region)
    )
    evidence.original = Path(temp_dir) / f"seal-{index}-original.jpg"
    save_region_crop(request.source, evidence.original, region)
    # Keep the full-page OCR text for the audit artifact below.  For a local
    # Paddle pass, do not feed it into seal matching: these page rows may be
    # black form labels, dates, or the printed ``签章要求`` that overlap the
    # QingTong box.  Paddle is deliberately driven by the colour-safe
    # derivatives below.  Non-Paddle legacy routes retain the old page-text
    # fallback because they do not have this local colour-isolation path.
    evidence.whole_text = extract_region_text(request.rows, region)
    if evidence.whole_text and not is_paddle_backend(request.ocr_backend):
        evidence.region_texts.append(evidence.whole_text)
    evidence.isolated = Path(temp_dir) / f"seal-{index}-isolated.png"
    save_isolated_seal(request.source, evidence.isolated, region)
    evidence.color_isolated = Path(temp_dir) / f"seal-{index}-color-isolated.png"
    save_color_isolated_seal(request.source, evidence.color_isolated, region)
    evidence.color_isolated_oriented = None
    evidence.ellipse_normalized = None
    evidence.orientation = {}
    evidence.orientation_anchor_text = ""
    # A round stamp's outer circle has no direction.  The configured strategy
    # either keeps the crop unchanged, uses a detected ``用章``/``专用章``
    # polygon as the direction anchor, or applies Paddle's four-way document
    # orientation classifier.  Only the color-safe derivative is rotated.
    if (
        is_paddle_backend(request.ocr_backend)
        and request.orientation_mode != "none"
        and (not evidence.rectangular or request.orientation_mode == "doc_ori")
    ):
        try:
            from .seal_orientation import (
                prepare_ellipse_stamp,
                prepare_round_stamp,
                prepare_round_stamp_doc_ori,
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
                    model_variant=variant_of(request.ocr_backend) or "mobile",
                )
            else:
                oriented, evidence.orientation = prepare_round_stamp(
                    evidence.color_isolated,
                    oriented_path,
                    model_variant=variant_of(request.ocr_backend) or "mobile",
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
    # audited instead of silently stretching inside the polar transform.
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
    if not evidence.rectangular and is_paddle_backend(request.ocr_backend):
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
            from .paddle_ocr import recognize_line

            band_rows = recognize_line(
                evidence.round_type_band,
                model_variant=variant_of(request.ocr_backend) or "mobile",
            )
            evidence.round_type_band_texts = [row.text for row in band_rows if row.text]
            # The focused band can contain a second short line such as
            # ``（1）``. Reuse the already detected text boxes when the
            # recognition-only model returns just the main line.
            detected_type_texts = evidence.orientation.get("type_row_texts", [])
            detected_anchor = detected_type_texts[0] if detected_type_texts else ""
            detected_suffixes = detected_type_texts[1:]
            if detected_anchor and detected_anchor not in evidence.round_type_band_texts:
                evidence.round_type_band_texts.insert(0, detected_anchor)
            if detected_anchor in evidence.round_type_band_texts:
                anchor_index = evidence.round_type_band_texts.index(detected_anchor)
                suffix = "".join(
                    value for value in detected_suffixes
                    if value and value not in evidence.round_type_band_texts
                )
                if suffix:
                    evidence.round_type_band_texts[anchor_index] = (
                        detected_anchor + suffix
                    )
            else:
                evidence.round_type_band_texts.extend(
                    value for value in detected_suffixes
                    if value and value not in evidence.round_type_band_texts
                )
            evidence.region_texts.extend(evidence.round_type_band_texts)
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
        try:
            if evidence.code_line is None or not evidence.code_line.is_file():
                raise ValueError("矩形编号章数字行未生成")
            if request.ocr_backend in {"paddle_v6", "paddle", "paddle_server"}:
                from .paddle_ocr import recognize_line

                code_rows = recognize_line(
                    evidence.code_line,
                    model_variant=(
                        "v6"
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
    # ``isolated`` is the black/white high-contrast audit image.  Keep it
    # available for visual inspection, but do not OCR it: the thresholded
    # comparison view is prone to turning form strokes into false stamp text
    # and is not an independent recognition channel.
    evidence.crop_text = ""
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
    evidence.oriented_texts = []
    if (
        evidence.orientation_anchor_text
        and float(evidence.orientation.get("confidence", 0.0) or 0.0)
        >= 0.70
    ):
        # This is the detector's own pre-rotation recognition, not a
        # completion from the requirement.  Keep it as evidence because
        # the re-detection after interpolation can clip the leftmost
        # ``收`` even though the anchor pass saw the complete phrase.
        evidence.oriented_texts.append(evidence.orientation_anchor_text)
    if evidence.color_isolated_oriented is not None:
        # ``prepare_round_stamp`` already detected the usable text polygons
        # before rotating the crop.  The focused type-row image is recognized
        # as one line above, so running a second full detector on the rotated
        # canvas only repeats work and can clip the leftmost ``收`` after
        # interpolation.  Reuse the pre-rotation anchor and the single-line
        # row result; the polar-unwrapped derivative remains the independent
        # source for the curved company name.
        evidence.oriented_texts.extend(evidence.round_type_band_texts)
    evidence.region_texts.extend(evidence.oriented_texts)
    ring_exclude_boxes = []
    source_type_box = evidence.orientation.get("type_row_box")
    if source_type_box is None and type_band_focus_box is not None:
        source_type_box = type_band_focus_box
    if source_type_box is not None:
        try:
            with Image.open(evidence.color_isolated) as image:
                mask_width, mask_height = image.size
            left, top, right, bottom = [float(value) for value in source_type_box]
            box_width = max(0.0, right - left)
            box_height = max(0.0, bottom - top)
            # A diagonal detector polygon can cover most of the circular crop
            # even though it represents only the centre ``收货专用章`` text.
            # Do not use such a broad box to erase the ring before unwrapping;
            # that was the reason some otherwise clear red ring lettering
            # produced a nearly blank black/white展开图.
            if (
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
                    "章型检测框覆盖过大，保留环形文字展开区域"
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
        save_unwrapped_seal(
            request.source,
            evidence.unwrapped,
            region,
            exclude_boxes=ring_exclude_boxes,
            elliptical=evidence.elliptical,
        )
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
    unwrap_rows = []
    try:
        if is_paddle_backend(request.ocr_backend) and not evidence.rectangular:
            # The polar output is a three-strip contact sheet.  Each strip is
            # one horizontal line, so split it before recognition instead of
            # asking the detector to rediscover three rows in a tall image.
            band_paths = save_unwrapped_seal_bands(
                evidence.unwrapped,
                evidence.unwrapped.with_name(
                    f"seal-{index}-unwrapped-primary-band"
                ),
            )
            from .paddle_ocr import recognize_line

            for band_path in band_paths:
                unwrap_rows.extend(
                    recognize_line(
                        band_path,
                        model_variant=variant_of(request.ocr_backend) or "mobile",
                    )
                )
        else:
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
    if (evidence.rectangular and request.orientation_mode == "polygon"
            and index not in request.orientation_resolved_indices):
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
        # stamp-type suffix across different transforms.  Retain both
        # providers as independent OCR evidence, then combine only text
        # actually recognized.
        # The raw crop is retained for display only; neither the primary nor
        # secondary route should spend an OCR call on it.
        evidence.secondary_original_texts = []
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
        # The black/white high-contrast crop is audit-only for both providers;
        # do not spend a secondary model call on the same non-safe image.
        evidence.secondary_crop_texts = []
        try:
            if is_paddle_backend(request.secondary_ocr_backend) and not evidence.rectangular:
                secondary_band_paths = save_unwrapped_seal_bands(
                    evidence.unwrapped,
                    evidence.unwrapped.with_name(
                        f"seal-{evidence.unwrapped.stem}-secondary-band"
                    ),
                )
                from .paddle_ocr import recognize_line

                secondary_rows = []
                for band_path in secondary_band_paths:
                    secondary_rows.extend(
                        recognize_line(
                            band_path,
                            model_variant=(
                                variant_of(request.secondary_ocr_backend) or "mobile"
                            ),
                        )
                    )
            else:
                secondary_rows = recognize_text(
                    evidence.unwrapped,
                    backend=request.secondary_ocr_backend,
                    min_text_height=0.04,
                )
            evidence.secondary_unwrap_texts = [
                row.text for row in secondary_rows if row.text
            ]
        except Exception:
            pass
        if evidence.code_line is not None and evidence.code_line.is_file():
            try:
                if request.secondary_ocr_backend in {"paddle_v6", "paddle", "paddle_server"}:
                    from .paddle_ocr import recognize_line

                    secondary_code_rows = recognize_line(
                        evidence.code_line,
                        model_variant=(
                            "v6"
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
                "shape": (
                    "矩形"
                    if evidence.rectangular
                    else "椭圆" if evidence.elliptical else "圆形"
                ),
                "qingtong_shape": region.qingtong_cls or "",
                "shape_source": "本地章色轮廓校验（清瞳章型仅作提示）",
                "ocr_backend": backend_label(request.ocr_backend),
                "secondary_ocr_backend": (
                    backend_label(request.secondary_ocr_backend)
                    if request.secondary_ocr_backend
                    else ""
                ),
                "original_url": f"{prefix}/seals/{evidence.original.name}",
                "isolated_url": f"{prefix}/seals/{evidence.isolated.name}",
                "color_isolated_url": f"{prefix}/seals/{evidence.color_isolated.name}",
                "color_isolated_oriented_url": (
                    f"{prefix}/seals/{evidence.color_isolated_oriented.name}"
                    if evidence.color_isolated_oriented is not None
                    and evidence.color_isolated_oriented.is_file()
                    else ""
                ),
                "ellipse_normalized_url": (
                    f"{prefix}/seals/{evidence.ellipse_normalized.name}"
                    if evidence.ellipse_normalized is not None
                    and evidence.ellipse_normalized.is_file()
                    else ""
                ),
                "orientation": evidence.orientation,
                "orientation_anchor_text": evidence.orientation_anchor_text,
                "round_type_band_url": (
                    f"{prefix}/seals/{evidence.round_type_band.name}"
                    if evidence.round_type_band is not None
                    and evidence.round_type_band.is_file()
                    else ""
                ),
                "round_type_band_text": " | ".join(evidence.round_type_band_texts),
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
                "color_isolated_oriented_text": " | ".join(evidence.oriented_texts),
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
            "color_isolated_oriented": evidence.color_isolated_oriented,
            "ellipse_normalized": evidence.ellipse_normalized,
            "orientation": evidence.orientation,
            "ring_type_mask_boxes": (
                [evidence.orientation["ring_type_mask_box"]]
                if evidence.orientation.get("ring_type_mask_box") else []
            ),
            "code_line": evidence.code_line,
            "unwrapped": evidence.unwrapped,
            "unwrapped_rotated": evidence.unwrapped_rotated,
            "color_isolated_rotations": evidence.color_isolated_rotations,
            "rotated": evidence.rotated if evidence.rectangular and evidence.rotated.is_file() else None,
            "rectangular": evidence.rectangular,
            "elliptical": evidence.elliptical,
            "shape": (
                "矩形"
                if evidence.rectangular
                else "椭圆" if evidence.elliptical else "圆形"
            ),
            "qingtong_shape": region.qingtong_cls or "",
            "evidence": _dedupe(
                evidence.region_texts + ([evidence.combined_text] if evidence.combined_text else [])
            ),
        }
    )
