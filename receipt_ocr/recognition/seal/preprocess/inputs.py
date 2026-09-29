"""Prepare one auditable input per physical stamp and text channel."""
from pathlib import Path
import shutil
import cv2
import numpy as np

from receipt_ocr.imaging.processing import (
    save_region_crop, save_color_isolated_seal, save_ellipse_normalized_seal,
    save_round_seal_type_band, save_unwrapped_seal,
    map_ellipse_box_to_normalized,
    round_seal_type_band_box,
)
from receipt_ocr.providers.paddle_runtime import variant_of
from .shapes import classify_shape
from .round import (
    prepare_round_stamp,
    prepare_round_stamp_doc_ori,
    prepare_round_stamp_combined,
    prepare_rectangular_stamp,
)
from .ellipse import prepare_ellipse_stamp

SHAPE_LABELS = {"round": "圆形", "ellipse": "椭圆", "rectangle": "矩形"}


def _symmetric_type_geometry(source: Path, orientation: dict) -> dict | None:
    """Mirror a detected horizontal type polygon in the upright stamp frame."""
    polygons = orientation.get("oriented_type_row_polygons")
    if not polygons:
        polygons = orientation.get("type_row_polygons")
    if not polygons:
        return None
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    center_x = width / 2.0
    source_polygons, mirrored_polygons, points = [], [], []
    for polygon in polygons:
        array = np.asarray(polygon, dtype=np.float32)
        if array.ndim != 2 or array.shape[0] < 3 or array.shape[1] != 2:
            continue
        mirrored = array.copy()
        mirrored[:, 0] = 2.0 * center_x - mirrored[:, 0]
        source_polygons.append(np.round(array).astype(np.int32).tolist())
        mirrored_polygons.append(np.round(mirrored).astype(np.int32).tolist())
        points.extend((array, mirrored))
    if not points:
        return None
    all_points = np.vstack(points)
    return {
        "center_x": round(center_x, 3),
        "focus_box": [
            max(0, int(np.floor(all_points[:, 0].min()))),
            max(0, int(np.floor(all_points[:, 1].min()))),
            min(width, int(np.ceil(all_points[:, 0].max()))),
            min(height, int(np.ceil(all_points[:, 1].max()))),
        ],
        "mask_polygons": source_polygons + mirrored_polygons,
    }


def _white_detected_type_region(
    source: Path,
    destination: Path,
    box: tuple[int, int, int, int] | None = None,
    *,
    polygons: list[list[list[int]]] | None = None,
) -> Path:
    """Copy the upright stamp and white only the type polygons and mirror."""
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取旋正章色整图：{source}")
    if polygons:
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        for polygon in polygons:
            points = np.asarray(polygon, dtype=np.int32)
            if points.ndim == 2 and points.shape[0] >= 3:
                cv2.fillPoly(mask, [points], 255)
        mask = cv2.dilate(mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
        image[mask > 0] = 255
    elif box:
        left, top, right, bottom = box
        image[max(0, top):min(image.shape[0], bottom),
              max(0, left):min(image.shape[1], right)] = 255
    else:
        raise ValueError("横向文字覆盖区域为空")
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
    artifact["color_isolated_url"] = add("color", color, "保留章色图")
    shape = classify_shape(source, region)
    artifact["shape"] = {"round": "圆形", "ellipse": "椭圆", "rectangle": "矩形"}[shape]
    artifact["shape_code"] = shape
    working = color
    orientation = {"mode": orientation_mode, "status": "保留原方向", "confidence": 0.0}
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
    normalization_source = working
    if shape == "ellipse":
        normalized = path("normalized")
        save_ellipse_normalized_seal(working, normalized, color=region.color)
        working = normalized
        artifact["ellipse_normalized_url"] = add("normalized", working, "椭圆拉伸校正图")

    # Always read the final color-safe geometry, never the original form crop.
    body_input = inputs[-1]
    body_input.update(
        ocr_input=shape == "rectangle",
        channel="body",
        label=("椭圆拉伸校正图 · 环形 OCR 几何图"
                if shape == "ellipse" else "章色识别图"),
    )
    artifact["body_input_id"] = body_input["id"]
    artifact["body_url"] = body_input["image_url"]
    # Round and oval stamps are read only through their center/type and ring
    # channels. The full color crop stays available as visual evidence and is
    # never allowed to compete with those two purpose-specific OCR inputs.
    jobs = [(body_input, working)] if shape == "rectangle" else []
    if shape != "rectangle":
        # Use the detector's polygon and its post-rotation mirror when a
        # partial horizontal row was found.  The union extends the OCR search
        # area without filling a broad rectangle over the ring artwork.
        symmetric_geometry = (
            _symmetric_type_geometry(working, orientation)
            if shape == "round" and orientation.get("partial_anchor")
            else None
        )
        source_focus = (
            symmetric_geometry["focus_box"]
            if symmetric_geometry
            else (
                orientation.get("oriented_type_row_exact_box")
                if artifact.get("color_isolated_oriented_url")
                else orientation.get("type_row_exact_box")
            )
        )
        focus = source_focus
        center = path("center")
        ring_input = path("ring-input")
        ring = path("ring")
        try:
            aligned = bool(artifact.get("color_isolated_oriented_url")) or shape == "ellipse"
            source_mask_box = source_focus
            if shape == "ellipse" and source_mask_box:
                # The oval is stretched after orientation. Carry the same
                # exact detector box through the crop/resize transform.
                focus = map_ellipse_box_to_normalized(
                    normalization_source, source_focus, color=region.color
                )
                mask_box = map_ellipse_box_to_normalized(
                    normalization_source, source_mask_box, color=region.color
                )
            else:
                mask_box = source_mask_box
            if mask_box:
                box = tuple(int(round(value)) for value in mask_box)
                save_round_seal_type_band(working, center, orientation_aligned=aligned, focus_box=focus)
                center_url = add("center", center, "检测到的横向文字区域（仅定位/章型 OCR）", "center")
                jobs.append((inputs[-1], center))
                artifact.update(round_type_band_url=center_url, round_type_band_text="", round_type_band_texts=[])

                # The ring OCR must consume this saved image. It is not an implicit
                # mask inside the unwrap function: reviewers can inspect the exact
                # pixels that reached OCR.
                _white_detected_type_region(
                    working,
                    ring_input,
                    box,
                    polygons=(
                        symmetric_geometry["mask_polygons"]
                        if symmetric_geometry
                        else None
                    ),
                )
                ring_input_label = (
                    "椭圆拉伸校正图 · 横向文字区域填白（环形 OCR 实际输入）"
                    if shape == "ellipse"
                    else (
                        "旋正章色图 · 横向文字框及对称框填白（环形 OCR 实际输入）"
                        if symmetric_geometry
                        else "旋正章色图 · 横向文字区域填白（环形 OCR 实际输入）"
                    )
                )
                artifact["ring_input_operation"] = (
                    "在椭圆拉伸校正图上，仅将 OCR dt_polys 合并外接框填白"
                    if shape == "ellipse"
                    else (
                        "在旋正章色整图上，仅将横向文字框及中轴线对称框填白"
                        if symmetric_geometry
                        else "在旋正章色整图上，仅将 OCR dt_polys 合并外接框填白"
                    )
                )
                artifact["ring_input_box"] = list(box)
                artifact["ring_input_box_source"] = (
                    "转正后横向文字框及中轴线对称多边形"
                    if symmetric_geometry
                    else "OCR dt_polys 外接框（无额外边距）"
                )
                if symmetric_geometry:
                    artifact["ring_input_center_x"] = symmetric_geometry["center_x"]
                    artifact["ring_input_polygons"] = symmetric_geometry["mask_polygons"]
            else:
                # A stamp may contain only circular company lettering. There is
                # no horizontal type row to mask in that case, so the corrected
                # full stamp is already the correct ring-OCR input.
                _copy_ring_input(working, ring_input)
                ring_input_label = (
                    "椭圆拉伸校正图 · 无横向文字，直接作为环形 OCR 实际输入"
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
                artifact.get("ellipse_normalized_url")
                if shape == "ellipse"
                else artifact.get("color_isolated_oriented_url") or artifact.get("color_isolated_url")
            )
            save_unwrapped_seal(ring_input, ring, None, color=region.color,
                                elliptical=shape == "ellipse", normalized=shape == "ellipse", single_line=True)
            ring_url = add("ring", ring, "圆环公司文字", "ring")
            jobs.append((inputs[-1], ring))
            artifact.update(unwrapped_url=ring_url, unwrapped_text="", unwrap_texts=[])
        except Exception as exc:
            artifact["errors"].append({"step": "ring_inputs", "message": str(exc)})
    return artifact, jobs
