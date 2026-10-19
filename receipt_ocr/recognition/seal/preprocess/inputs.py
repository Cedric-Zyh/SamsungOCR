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
from receipt_ocr.providers.paddle_runtime import variant_of
from .shapes import classify_shape
from .round import prepare_round_stamp
from .doc_orientation import prepare_round_stamp_doc_ori
from .combined_orientation import prepare_round_stamp_combined
from .rectangle_orientation import prepare_rectangular_stamp
from .ellipse import prepare_ellipse_stamp
from .number import prepare_number_input
from .number_recovery import recover_number_punctuation
from ..ocr.interface import OcrReader

SHAPE_LABELS = {"round": "圆形", "ellipse": "椭圆", "rectangle": "矩形"}


def _white_detected_type_region(
    source: Path,
    destination: Path,
    box: tuple[int, int, int, int] | None = None,
    *,
    polygons: list[list[list[int]]] | None = None,
    mask_destination: Path | None = None,
) -> Path:
    """White only ink wholly inside observed text polygons.

    Detection boxes may overlap the rim. Components substantially crossing a
    polygon's boundary can be part of the rim or a neighbour, so retain them.
    No dilation, mirrored box, union rectangle, or change outside the mask.
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
    working = color
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
                corrected, orientation = prepare_round_stamp_combined(color, target, model_variant=variant_of(provider) or "v6")
            else:
                prepare = prepare_ellipse_stamp if shape == "ellipse" else prepare_round_stamp
                corrected, orientation = prepare(color, target, model_variant=variant_of(provider) or "v6")
            if corrected is not None:
                working = Path(corrected)
                artifact["color_isolated_oriented_url"] = add("oriented", working, "方向校正图")
        except Exception as exc:
            artifact["errors"].append({"step": "orientation", "message": str(exc)})
    artifact["orientation"] = orientation
    if shape == "ellipse":
        artifact["ring_geometry"] = "ellipse_direct_normals"

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
                box = tuple(int(round(value)) for value in mask_box)
                save_round_seal_type_band(working, center, orientation_aligned=aligned,
                                          focus_box=orientation.get("oriented_type_row_read_box") or focus)
                center_url = add("center", center, "检测到的横向文字区域（仅定位/章型 OCR）", "center")
                jobs.append((inputs[-1], center))
                artifact.update(round_type_band_url=center_url, round_type_band_text="", round_type_band_texts=[])

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
                )
                artifact["ring_input_mask_url"] = add("type-mask", mask_path, "横排文字实际填白范围")
                ring_input_label = "旋正章色图 · 仅横排文字笔画填白（环形展开来源）"
                artifact["ring_input_operation"] = "仅填白检测文字多边形内的独立笔画，保留跨边界的外圈和相邻文字，再展开读取环字"
                artifact["ring_input_mask_method"] = "contained_text_ink"
                artifact["ring_input_box"] = list(box)
                artifact["ring_input_box_source"] = "检测文字范围（仅定位，实际覆盖以笔画掩码为准）"
                if mask_polygons:
                    artifact["ring_input_polygons"] = mask_polygons
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
            ring_guide = None
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
            save_ring_text_band_preview(ring_input, ring_band, color=region.color, ring_guide=ring_guide)
            artifact["ring_text_band_url"] = add(
                "ring-text-band",
                ring_band,
                "环形文字带（展开前）",
            )
            save_unwrapped_seal(ring_input, ring, None, color=region.color,
                                elliptical=True, normalized=True, single_line=True,
                                direct_ellipse=shape == "ellipse", ring_guide=ring_guide)
            if shape == "round":
                company_ring = path("ring-company")
                selection = save_company_ring_input(ring, company_ring)
                if selection:
                    artifact["unwrapped_full_url"] = add(
                        "ring-full", ring, "完整环形展开图（含小字号段，仅供核对）"
                    )
                    artifact["ring_company_selection"] = selection
                    ring = company_ring
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
