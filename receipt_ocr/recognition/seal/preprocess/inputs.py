"""Prepare one auditable input per physical stamp and text channel."""
from pathlib import Path
import shutil
import cv2
import numpy as np

from receipt_ocr.imaging.crops import save_region_crop, save_color_isolated_seal

from receipt_ocr.imaging.bands import save_round_seal_type_band

from receipt_ocr.imaging.unwrap import save_ring_text_band_preview, save_unwrapped_seal
from receipt_ocr.imaging.ring_border import trace_ellipse_border, save_ring_border_overlay
from receipt_ocr.imaging.ring_company import save_company_ring_input
from receipt_ocr.imaging.ring_seam import save_ring_with_blank_seam
from receipt_ocr.providers.paddle_runtime import variant_of
from .shapes import classify_shape
from .round import prepare_round_stamp
from .doc_orientation import prepare_round_stamp_doc_ori
from .combined_orientation import prepare_round_stamp_combined
from .rectangle_orientation import prepare_rectangular_stamp
from .rectangle_rows import prepare_fixed_rectangle_rows
from .rectangle_layout import prepare_frame_layout
from .ellipse import prepare_ellipse_stamp
from .number import prepare_number_input
from .number_mask import white_verified_number
from .number_recovery import recover_number_punctuation
from .red_enhancement import enhance_oriented_seal
from .oval_ring import prepare_oval_ring
from .strong_border import prepare_strong_border_ring
from .han_ring import prepare_han_ring
from .multi_zone import prepare_multi_zone
from .local_type_row import prepare_local_type_row
from .horizontal_consensus import prepare_horizontal_consensus, protect_consensus_border
from ..ocr.interface import OcrReader

SHAPE_LABELS = {"round": "圆形", "ellipse": "椭圆", "rectangle": "矩形"}


def _white_detected_type_region(
    source: Path,
    destination: Path,
    box: tuple[int, int, int, int] | None = None,
    *,
    polygons: list[list[list[int]]] | None = None,
    mask_destination: Path | None = None,
    verified_interior: list | None = None,
    verified_coverage: np.ndarray | None = None,
) -> Path:
    """White observed row ink, protecting the border and outside pixels.

    Raw detector polygons retain components crossing their edges. A complete
    glyph-measured row may clip joined strokes, but only within its verified
    polygon and the separately measured border-protection interior.
    """
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取旋正章色整图：{source}")
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    if polygons:
        for polygon in polygons:
            points = np.rint(polygon).astype(np.int32)
            if points.ndim == 2 and points.shape[0] >= 3:
                cv2.fillPoly(mask, [points], 255)
    elif box:
        left, top, right, bottom = box
        mask[max(0, top):min(image.shape[0], bottom),
              max(0, left):min(image.shape[1], right)] = 255
    else:
        raise ValueError("横向文字覆盖区域为空")
    ink = np.any(image < 255, axis=2).astype(np.uint8)
    if verified_interior is not None or verified_coverage is not None:
        # This branch is only for a measured/detected, OCR-verified full row.
        # A stroke joined to an outside table remnant must not retain the
        # whole glyph. Clip at the measured row and protect the observed rim.
        if verified_coverage is not None:
            if verified_coverage.shape != mask.shape:
                raise ValueError("横排文字覆盖范围与旋正图尺寸不一致")
            interior = verified_coverage
        else:
            interior = np.zeros_like(mask)
            cv2.fillPoly(interior, [np.rint(verified_interior).astype(np.int32)], 255)
        selected = (ink > 0) & (mask > 0) & (interior > 0)
        image[selected] = 255
        if mask_destination is not None:
            mask_destination.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(mask_destination), selected.astype(np.uint8) * 255):
                raise ValueError("横排文字实际填白范围保存失败")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(destination), image):
            raise ValueError("旋正章色整图的横向文字区域白底覆盖图保存失败")
        return destination
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    inside = np.bincount(labels[mask > 0], minlength=len(stats))
    contained = inside == stats[:, cv2.CC_STAT_AREA]
    contained[0] = False
    selected = contained[labels] & (mask > 0)
    # Very pale interpolation halos can connect a whole line to neighbouring
    # artwork. Separate its solid ink for localization, then assign the nearby
    # soft pixels back to their nearest stroke. The original OCR image and all
    # pixels outside the observed polygons remain unchanged.
    strong = (image.max(axis=2).astype(np.int16) - image.min(axis=2) >= 16).astype(np.uint8)
    if strong.any():
        distance, nearest = cv2.distanceTransformWithLabels(
            1 - strong, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP,
        )
        sizes = np.bincount(nearest[strong > 0])
        inside = np.bincount(nearest[(strong > 0) & (mask > 0)], minlength=len(sizes))
        belongs_to_row = inside >= .98 * sizes
        belongs_to_row[0] = False
        selected |= ((mask > 0) & (ink > 0) & belongs_to_row[nearest] & (distance <= 6))
    image[selected] = 255
    if mask_destination is not None:
        mask_destination.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(mask_destination), selected.astype(np.uint8) * 255):
            raise ValueError("横排文字实际填白范围保存失败")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image):
        raise ValueError("旋正章色整图的横向文字区域白底覆盖图保存失败")
    return destination


def _copy_ring_input(source: Path, destination: Path) -> Path:
    """Persist an unmodified stamp image when no horizontal type row exists."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def prepare_inputs(source, region, index, directory, url_prefix, provider, orientation_mode):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    inputs = []
    artifact = {"schema_version": 2, "index": index, "region_id": f"seal-{index}",
                "color": region.color, "inputs": inputs, "reads": [], "errors": []}

    def path(name):
        return directory / f"seal-{index}-{name}.png"

    def add(name, file, label, channel=None):
        url = f"{url_prefix.rstrip('/')}/{file.name}" if url_prefix else ""
        inputs.append({"id": f"seal-{index}:{name}", "name": name, "label": label,
                       "channel": channel, "image_url": url, "ocr_input": channel is not None})
        return url

    original = path("original")
    save_region_crop(source, original, region)
    artifact["original_url"] = add("original", original, "印章原图 · 仅供核对")
    color = path("color")
    save_color_isolated_seal(source, color, region)
    artifact["color_isolated_url"] = add("color", color, "章色分离增强图")
    shape = classify_shape(source, region)
    artifact["shape"] = {"round": "圆形", "ellipse": "椭圆", "rectangle": "矩形"}[shape]
    artifact["shape_code"] = shape
    if (shape == "rectangle" and region.color == "red" and orientation_mode != "none"
            and variant_of(provider) == "v6"):
        try:
            metadata, frame_inputs, jobs = prepare_frame_layout(
                color, directory, index, url_prefix, provider, orientation_mode, OcrReader(),
            )
        except Exception as exc:
            # Missing/broken rims retain the existing text-box path.
            artifact["rectangular_frame_fallback"] = str(exc)
        else:
            artifact.update(metadata)
            inputs.extend(frame_inputs)
            return artifact, jobs
    working = color
    consensus = None
    orientation = {"mode": orientation_mode, "status": "保留原方向", "confidence": 0.0, "rotation_steps": []}
    if orientation_mode != "none":
        try:
            target = path("oriented")
            if shape == "rectangle":
                # Rectangular stamps usually have only a company row and a
                # number row. They cannot rely on the round-stamp ``专用章``
                # direction anchor, so deskew from the longest horizontal
                # OCR row and add a small white margin first.
                corrected, orientation = prepare_rectangular_stamp(
                    color, target, model_variant=variant_of(provider) or "v6"
                )
            elif orientation_mode == "doc_ori":
                corrected, orientation = prepare_round_stamp_doc_ori(color, target)
            elif orientation_mode == "combined":
                model_variant = variant_of(provider) or "v6"
                corrected, orientation = prepare_round_stamp_combined(color, target, model_variant=model_variant)
                if shape == "round" and model_variant == "v6":
                    try:
                        consensus = prepare_horizontal_consensus(
                            original, color, path("consensus-oriented"), reader=OcrReader(), provider=provider,
                            model_variant=model_variant, prior=orientation, ink_color=region.color,
                        )
                    except Exception as exc:
                        artifact["horizontal_consensus_fallback"] = str(exc)
                if consensus is not None:
                    corrected, orientation = consensus["working"], consensus["orientation"]
                    artifact["horizontal_row_consensus"] = consensus["audit"]
                elif not orientation.get("type_band_text"):
                    # The usual cleaned view can lose a slanted row even when
                    # native source chroma still carries it. Retry only failures,
                    # retaining the existing geometry and full-row verification.
                    try:
                        orientation_source = enhance_oriented_seal(
                            source, region, color, path("orientation-source"), {"rotation_steps": []})
                        if orientation_source is not None:
                            retry_image, retry = prepare_round_stamp_combined(
                                orientation_source, path("source-oriented"),
                                model_variant=model_variant, retry_large_angles=True)
                            artifact["source_orientation_attempt"] = retry
                            if retry.get("type_band_text"):
                                artifact["orientation_before_source_retry"] = orientation
                                artifact["orientation_source_url"] = add(
                                    "orientation-source", orientation_source, "方向检测 · 原图章色增强")
                                corrected, orientation = retry_image or orientation_source, retry
                    except Exception as exc:
                        artifact["source_orientation_fallback"] = str(exc)
            else:
                prepare = prepare_ellipse_stamp if shape == "ellipse" else prepare_round_stamp
                corrected, orientation = prepare(color, target, model_variant=variant_of(provider) or "v6")
            if corrected is not None:
                working = Path(corrected)
                artifact["color_isolated_oriented_url"] = add("oriented", working, "方向校正图")
        except Exception as exc:
            artifact["errors"].append({"step": "orientation", "message": str(exc)})
    artifact["orientation"] = orientation
    unrotated_round = shape == "round" and working == color
    if (consensus is None and shape != "rectangle"
            and (artifact.get("color_isolated_oriented_url") or unrotated_round)):
        try:
            # A ring-only stamp may have no horizontal orientation anchor.
            # Keeping its direction must not skip source-chroma cleanup:
            # the old enhanced dark remnants otherwise become false glyphs.
            # The untouched color canvas has a known identity mapping even
            # when the failed orientation search omits rotation_steps.
            enhancement_orientation = {"rotation_steps": []} if unrotated_round else orientation
            enhanced = enhance_oriented_seal(
                source, region, working, path("enhanced"), enhancement_orientation,
            )
            if enhanced is not None:
                working = enhanced
                color_label = "红章" if region.color == "red" else "蓝章"
                artifact["color_isolated_oriented_url"] = add(
                    "enhanced", working, f"{color_label} · 原色结合增强" if unrotated_round else f"旋正{color_label} · 原色结合增强",
                )
                artifact[f"{region.color}_enhancement"] = {
                    "method": "source_chroma_supported_blend",
                    "chroma_gain": 2.0,
                    "previous_strength_weight": 0.15,
                    "coordinate_frame": "unrotated" if unrotated_round else "oriented",
                }
        except Exception as exc:
            artifact["errors"].append({"step": f"{region.color}_enhancement", "message": str(exc)})
    if shape == "ellipse":
        artifact["ring_geometry"] = "ellipse_proportional"

    # Always read the final color-safe geometry, never the original form crop.
    body_input = inputs[-1]
    body_input.update(
        ocr_input=shape == "rectangle",
        channel="body",
        label=("旋正椭圆原比例图 · 环形 OCR 几何图"
                if shape == "ellipse" else "章色识别图"),
    )
    artifact["body_input_id"] = body_input["id"]
    artifact["body_url"] = body_input["image_url"]
    # Round and oval stamps are read only through their center/type and ring
    # channels. The full color crop stays available as visual evidence and is
    # never allowed to compete with those two purpose-specific OCR inputs.
    jobs = [(body_input, working)] if shape == "rectangle" else []
    if shape == "rectangle":
        # Orientation already detected and recognized the color image. Reuse
        # that localization instead of asking a padded image for smaller boxes.
        if "detected_boxes" not in orientation:
            orientation["detected_boxes"] = OcrReader().detect_boxes(
                color, provider=provider, model_variant=variant_of(provider) or "v6",
            )
        fixed_rows, rotated = prepare_fixed_rectangle_rows(
            color, working, orientation, directory, index,
        )
        artifact["rectangular_layout"] = "single_detection"
        artifact["rectangular_fixed_rows"] = fixed_rows
        artifact["rectangular_row_urls"] = []
        body_input.update(ocr_input=False, channel=None)
        jobs = []
        if not rotated:
            # These reads came from the unpadded color image, not the display
            # crops or the oriented preview. Keep their provenance accurate.
            detected_input = next(item for item in inputs if item["name"] == "color")
            detected_input.update(
                channel="body", ocr_input=True, detected_rows=fixed_rows,
                label="章色图 · 首次文字检测与识别",
            )
            artifact.update(body_input_id=detected_input["id"], body_url=detected_input["image_url"])
            jobs.append((detected_input, color))
        for row_index, row in enumerate(fixed_rows):
            row_path = Path(row["path"])
            url = add(
                f"rectangle-row-{row_index}", row_path, f"矩形印章文字行 {row_index + 1}",
                "body" if rotated else None,
            )
            artifact["rectangular_row_urls"].append(url)
            if rotated:
                inputs[-1].update(read_mode="line", rectangle_row_index=row_index)
                jobs.append((inputs[-1], row_path))
    if shape in {"round", "ellipse"}:
        try:
            layout_evidence = {}
            plan = prepare_multi_zone(working, directory, index, reader=OcrReader(),
                                      provider=provider, color=region.color,
                                      ring_guide=consensus["guide"] if consensus is not None else None,
                                      orientation=orientation, layout_evidence=layout_evidence)
            if layout_evidence:
                artifact["layout_selection"] = layout_evidence
            if plan is not None:
                artifact["multi_zone_layout"] = {
                    "method": plan["method"], "rows": plan["rows"], "arcs": plan["arcs"],
                    "expected_inner_roles": plan.get("expected_inner_roles", []),
                    "status": "prepared",
                    "overlay_url": add("zones-regions", plan["overlay"], "自动文字分区"),
                }
                artifact["ring_input_url"] = add("zones-ring-input", plan["ring_input"], "仅内部文字填白")
                artifact["ring_input_mask_url"] = add("zones-mask", plan["mask"], "实际填白笔画")
                artifact["ring_input_source_url"] = body_input["image_url"]
                artifact["ring_input_source_name"] = Path(working).name
                artifact["ring_input_name"] = Path(plan["ring_input"]).name
                artifact["ring_border_detection"] = plan["border"]
                artifact["ring_input_mask_method"] = "multi_zone_ink"
                artifact["ring_input_operation"] = (
                    "分别识别章内部与章型行，仅填白内部文字笔画，再展开公司名称"
                    if plan["method"] == "ellipse_department_rows"
                    else "分别识别章型与编号，仅填白内部文字笔画，沿连续外环完整展开公司名称"
                    if plan["method"] == "ellipse_continuous_name"
                    else "自动分离内部多行，保护外圈两端，上下弧分别展开"
                )
                jobs = []
                for name, image_path, channel, label in plan["jobs"]:
                    url = add(f"zones-{name}", image_path, label, channel)
                    inputs[-1]["zone_name"] = name
                    jobs.append((inputs[-1], image_path))
                    if channel == "ring":
                        artifact.update(unwrapped_url=url, unwrapped_text="", unwrap_texts=[])
                return artifact, jobs
        except Exception as exc:
            artifact["multi_zone_fallback"] = str(exc)
    if shape != "rectangle":
        # Reading may need whitespace around a row; masking must use only
        # observed polygons in the final frame, without a mirrored extension.
        source_focus = (orientation.get("oriented_type_row_exact_box")
                        or orientation.get("type_row_exact_box"))
        mask_polygons = (orientation.get("oriented_type_row_polygons")
                         or orientation.get("type_row_polygons"))
        focus = source_focus
        center = path("center")
        ring_input = path("ring-input")
        ring = path("ring")
        try:
            aligned = bool(artifact.get("color_isolated_oriented_url")) or shape == "ellipse"
            mask_box = source_focus
            if mask_box:
                # Full-row localization and whole-stamp rotation are already
                # complete. Cropping and masking now share that final frame.
                refined = orientation.get("horizontal_row_localization")
                local_row = None
                if refined is None and consensus is None:
                    try:
                        local_row = prepare_local_type_row(
                            working, center, orientation, reader=OcrReader(),
                            provider=provider, color=region.color,
                        )
                    except Exception as exc:
                        artifact["horizontal_row_refinement_error"] = str(exc)
                if local_row:
                    points = np.asarray(local_row["polygon"])
                    mask_box = [*points.min(0), *points.max(0)]
                    mask_polygons = [local_row["polygon"]]
                    focus = mask_box
                    artifact["horizontal_row_localization"] = {
                        key: value for key, value in local_row.items() if key != "coverage"
                    }
                if refined is not None:
                    artifact["horizontal_row_localization"] = {
                        key: value for key, value in refined.items() if key != "protected_interior"
                    }
                if consensus is not None:
                    shutil.copyfile(consensus["center"], center)
                elif local_row is None:
                    save_round_seal_type_band(working, center, orientation_aligned=aligned,
                                              focus_box=orientation.get("oriented_type_row_read_box") or focus)
                box = tuple(int(round(value)) for value in mask_box)
                center_url = add("center", center, "检测到的横向文字区域（仅定位/章型 OCR）", "center")
                jobs.append((inputs[-1], center))
                artifact.update(round_type_band_url=center_url, round_type_band_text="", round_type_band_texts=[])
                verified_path = Path(orientation.get("type_band_path") or "")
                if verified_path.is_file() and orientation.get("type_band_text"):
                    center_input = jobs[-1][0]
                    verified_url = add("verified-type-row", verified_path, "旋正时已验证的横排文字")
                    center_input["verified_type_read"] = {
                        "text": orientation["type_band_text"],
                        "confidence": orientation.get("type_band_confidence", 0),
                        "image_url": verified_url,
                    }

                # The type line and a short number below it are separate OCR
                # inputs. Do not enlarge the ring mask to include this search.
                try:
                    number = path("number")
                    number_evidence = prepare_number_input(
                        working, number, focus, reader=OcrReader(), provider=provider,
                    )
                    artifact["stamp_number_detection"] = number_evidence
                    if number_evidence["status"] == "detected":
                        artifact["stamp_number_url"] = add("number", number, "章内编号", "number")
                        jobs.append((inputs[-1], number))
                except Exception as exc:
                    artifact["errors"].append({"step": "number_input", "message": str(exc)})

                # The ring OCR must consume this saved image. It is not an implicit
                # mask inside the unwrap function: reviewers can inspect the exact
                # pixels that reached OCR.
                mask_path = path("type-mask")
                _white_detected_type_region(
                    working, ring_input, box, polygons=mask_polygons,
                    mask_destination=mask_path,
                    verified_interior=refined["protected_interior"] if refined else None,
                    verified_coverage=local_row["coverage"] if local_row else None,
                )
                if consensus is not None:
                    protect_consensus_border(working, ring_input, mask_path, consensus["guide"])
                artifact["ring_input_mask_url"] = add("type-mask", mask_path, "横排文字实际填白范围")
                ring_input_label = "旋正章色图 · 仅横排文字笔画填白（环形展开来源）"
                artifact["ring_input_operation"] = "仅填白检测文字多边形内的独立笔画，保留跨边界的外圈和相邻文字，再展开读取环字"
                artifact["ring_input_mask_method"] = "contained_text_ink"
                artifact["ring_input_box"] = list(box)
                artifact["ring_input_box_source"] = "检测文字范围（仅定位，实际覆盖以笔画掩码为准）"
                if refined:
                    artifact["ring_input_mask_method"] = "verified_glyph_row"
                    artifact["ring_input_operation"] = "按实测字高和基线定位完整横排文字，识别验证后仅填白该行笔画，保护外边框，再展开读取环字"
                    artifact["ring_input_box_source"] = "完整横排文字范围（按字形实测，外边框保护）"
                if local_row:
                    artifact["ring_input_mask_method"] = "verified_local_row"
                    artifact["ring_input_operation"] = "局部定位并验证完整横排，按同一文字范围读取和填白；可测量时按字形收紧并保护外边框，再展开读取环字"
                    artifact["ring_input_box_source"] = "验证后的完整横排文字范围"
                if mask_polygons:
                    artifact["ring_input_polygons"] = mask_polygons
                # A separate number read remains an independent field, but
                # must not be read again as part of the circular company name.
                number_mask_path = path("number-mask")
                try:
                    number_mask = white_verified_number(
                        ring_input, path("number"), artifact.get("stamp_number_detection") or {},
                        number_mask_path, mask_path, reader=OcrReader(), provider=provider,
                    )
                    artifact["ring_number_mask"] = number_mask
                    if number_mask["status"] == "applied":
                        artifact["ring_number_mask_url"] = add(
                            "number-mask", number_mask_path, "编号实际填白笔画"
                        )
                        ring_input_label = "旋正章色图 · 横排文字与已确认编号笔画填白（环形展开来源）"
                        artifact["ring_input_operation"] += "；已确认的章内编号也按独立紧框填白"
                except Exception as exc:
                    artifact["errors"].append({"step": "number_mask", "message": str(exc)})
            else:
                # A stamp may contain only circular company lettering. There is
                # no horizontal type row to mask in that case, so the corrected
                # full stamp is already the correct ring-OCR input.
                _copy_ring_input(working, ring_input)
                ring_input_label = (
                    "旋正椭圆原比例图 · 无横向文字，直接作为环形 OCR 实际输入"
                    if shape == "ellipse"
                    else "旋正章色图 · 无横向文字，直接作为环形 OCR 实际输入"
                )
                artifact["ring_input_operation"] = "未检测到横向文字，直接使用校正后的整章图进行环形 OCR"
                artifact["ring_input_box_source"] = "无横向文字框，未做覆盖"

            ring_input_url = add(
                "ring_input",
                ring_input,
                ring_input_label,
            )
            artifact["ring_input_url"] = ring_input_url
            artifact["ring_input_source_url"] = (
                artifact.get("color_isolated_oriented_url") or artifact.get("color_isolated_url")
            )
            # The ring input is already a corrected, white-covered stamp
            # image.  Use the ellipse-aware sampler for both round and oval
            # stamps: the outer border remains visible after the cover, while
            # the inner border may be missing and must be estimated.
            ring_guide = consensus["guide"] if consensus is not None else None
            if consensus is not None:
                artifact["ring_geometry"] = "fixed_pre_mask_ellipse"
            if shape == "ellipse":
                # Measure the intact ring before the centre-row white mask can
                # cut through it. All downstream images reuse these coordinates.
                ring_guide = trace_ellipse_border(working, color=region.color)
                if ring_guide is not None:
                    overlay = path("ring-border")
                    save_ring_border_overlay(working, overlay, ring_guide)
                    artifact["ring_border_url"] = add("ring-border", overlay, "检测到的椭圆边框")
                    artifact["ring_border_detection"] = ring_guide.summary()
                    artifact["ring_geometry"] = "ellipse_observed_border"
                else:
                    artifact["ring_border_detection"] = {
                        "method": "ellipse_estimate", "note": "可见边框不足，使用椭圆几何估计"
                    }
            ring_band = path("ring-text-band")
            save_ring_text_band_preview(ring_input, ring_band, color=region.color,
                                        ring_guide=ring_guide, proportional=shape == "ellipse")
            artifact["ring_text_band_url"] = add(
                "ring-text-band",
                ring_band,
                "环形文字带（展开前）",
            )
            save_unwrapped_seal(ring_input, ring, None, color=region.color,
                                elliptical=True, normalized=True, single_line=True,
                                direct_ellipse=shape == "ellipse", ring_guide=ring_guide)
            if shape == "ellipse" and consensus is None and ring_guide is None:
                try:
                    strong = prepare_strong_border_ring(
                        working, ring_input, path("ring-strong"), color=region.color,
                        reader=OcrReader(), provider=provider,
                    )
                    if strong is not None:
                        artifact["unwrapped_radial_url"] = add("ring-radial", ring, "原展开图（对照）")
                        ring, ring_guide = strong["ring"], strong["guide"]
                        artifact["ring_strong_border"] = strong["evidence"]
                        artifact["ring_strong_border"]["check_image_url"] = add(
                            "ring-strong-check", strong["check_ring"], "另一档边框定位展开核对")
                        artifact["ring_geometry"] = "stable_strong_ellipse_normals"
                        artifact["ring_border_detection"] = strong["evidence"].copy()
                        artifact["ring_border_url"] = add(
                            "ring-border", strong["border"], "深色笔画稳定定位的椭圆边框")
                        save_ring_text_band_preview(ring_input, ring_band, color=region.color,
                                                    ring_guide=ring_guide, proportional=True)
                except Exception as exc:
                    artifact["errors"].append({"step": "strong_border", "message": str(exc)})
            if shape == "ellipse" and consensus is None and not artifact.get("ring_strong_border"):
                try:
                    oval = prepare_oval_ring(
                        source, region, working,
                        path("type-mask") if artifact.get("ring_input_mask_url") else None,
                        ring_guide, [] if working == color else orientation.get("rotation_steps"),
                        path("ring-native"), reader=OcrReader(), provider=provider,
                    )
                    if oval is not None:
                        artifact["unwrapped_radial_url"] = add(
                            "ring-radial", ring, "原展开图（对照）")
                        ring = oval["ring"]
                        ring_guide = oval["guide"]
                        artifact["ring_source_chroma"] = oval["evidence"]
                        artifact["ring_geometry"] = "fitted_ellipse_normals"
                        artifact["ring_input_source_url"] = add(
                            "ring-native-source", oval["source"], "原图淡色笔画增强")
                        artifact["ring_input_url"] = add(
                            "ring-native-input", oval["input"], "原图章色 · 横排笔画填白（环形展开来源）")
                        save_ring_text_band_preview(
                            oval["input"], ring_band, color=region.color,
                            ring_guide=ring_guide, proportional=True)
                        artifact["ring_border_detection"].update(
                            sampling="fitted_ellipse_normals",
                            unwrap_size=list(ring_guide.map_x.shape[::-1]))
                except Exception as exc:
                    artifact["errors"].append({"step": "oval_source_chroma", "message": str(exc)})
            han = None
            if shape == "round" and focus is None:
                try:
                    han = prepare_han_ring(ring_input, directory, index,
                                           reader=OcrReader(), provider=provider,
                                           color=region.color)
                except Exception as exc:
                    # Optional selection must leave the usable original path.
                    artifact["errors"].append({"step": "han_ring_candidate", "message": str(exc)})
                if han is not None:
                    artifact["ring_han_selection"] = han["evidence"]
                    artifact["unwrapped_full_url"] = add(
                        "ring-full", han["full"], "完整展开图（其他文字与数字仅供核对）")
                    artifact["ring_text_band_url"] = add(
                        "han-band", han["band"], "自动定位的汉字弧段（展开前）")
                    artifact["ring_geometry"] = "han_outer_frame_proportional"
                    ring = han["ring"]
            if shape == "round" and han is None:
                company_ring = path("ring-company")
                selection = save_company_ring_input(ring, company_ring)
                if selection:
                    artifact["unwrapped_full_url"] = add(
                        "ring-full", ring, "完整环形展开图（含小字号段，仅供核对）"
                    )
                    artifact["ring_company_selection"] = selection
                    ring = company_ring
            # Apply the tested gap gate to the final company strip, after any
            # serial-number selection. OCR and the displayed preview must use
            # the same derivative; retain its original for pixel-level review.
            try:
                shifted_ring = path("ring-seam")
                seam = save_ring_with_blank_seam(ring, shifted_ring)
                artifact["ring_seam_adjustment"] = seam
                if seam["applied"]:
                    artifact["unwrapped_before_seam_url"] = add(
                        "ring-before-seam", ring, "调整起点前的环形展开图（仅供核对）"
                    )
                    ring = shifted_ring
            except Exception as exc:
                # Optional ordering must not suppress a usable OCR input.
                artifact["errors"].append({"step": "ring_seam", "message": str(exc)})
            ring_url = add("ring", ring, "圆环公司文字", "ring")
            jobs.append((inputs[-1], ring))
            artifact.update(unwrapped_url=ring_url, unwrapped_text="", unwrap_texts=[])
        except Exception as exc:
            artifact["errors"].append({"step": "ring_inputs", "message": str(exc)})
        # Preserve the ring/centre OCR pixels and orientation decision. Only
        # after those inputs are saved can a verified local number repair
        # update the full stamp displayed/registered as the standard.
        try:
            restored = path("number-restored")
            number_original = path("number-original")
            detection, recovery = recover_number_punctuation(
                source, region, working, restored, path("number"), number_original,
                focus, orientation, artifact.get("stamp_number_detection") or {},
                reader=OcrReader(), provider=provider,
            )
            if recovery["status"] != "not_needed":
                artifact["stamp_number_recovery"] = recovery
            if recovery["status"] == "restored":
                artifact["stamp_number_detection"] = detection
                recovery["original_number_url"] = add("number-original", number_original, "编号原图笔画核对")
                restored_url = add("number-restored", restored, "旋正章色图 · 保留原图编号笔画")
                artifact.update(color_isolated_oriented_url=restored_url, body_url=restored_url,
                                body_input_id=inputs[-1]["id"])
        except Exception as exc:
            artifact["errors"].append({"step": "number_recovery", "message": str(exc)})
    return artifact, jobs
