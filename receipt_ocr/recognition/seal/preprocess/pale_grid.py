"""Recover only complete, pale red table stamps before any circular unwrapping."""
from pathlib import Path

import cv2

from receipt_ocr.imaging.io import _read_image, _write_stage_image
from receipt_ocr.imaging.pale_seal import enhance_pale_red, pale_red_statistics
from .orientation_geometry import _rotation_geometry
from .receiving_grid import measure_receiving_grid


def prepare_pale_grid(source, directory, index, prefix, mode, *, color="red", audit=None):
    audit = audit if audit is not None else {}
    if color != "red":
        audit.update(status="skipped", reason="unsupported_ink_color")
        return None
    original = _read_image(source)
    statistics = pale_red_statistics(original)
    if statistics is None:
        audit.update(status="skipped", reason="ink_not_pale")
        return None
    measured = measure_receiving_grid(original)
    if not measured["accepted"]:
        audit.update(status="fallback", reason=measured["reason"], **statistics,
                     geometry=measured.get("metrics", {}))
        return None
    audit.update(status="applied", reason=measured["reason"], **statistics,
                 fragmented_rule_recovery=measured["metrics"]["fragmented_rule_recovery"])
    # Repeat the verified reference resize and rotation, with the source
    # colour enhancement performed first, exactly as in the offline trial.
    reference_height = round(original.shape[0]*1000/original.shape[1])
    enhanced = cv2.resize(enhance_pale_red(original), (1000, reference_height))
    matrix, width, height = _rotation_geometry(1000, reference_height, measured["angle"])
    enhanced = cv2.warpAffine(enhanced, matrix, (width, height), flags=cv2.INTER_CUBIC,
                              borderValue=(255, 255, 255))
    if enhanced.shape != measured["image"].shape:
        raise ValueError("淡色增强与矩形定位图片坐标不一致")
    inputs, jobs = [], []

    def save(name, pixels, label, channel=None):
        path = Path(directory)/f"seal-{index}-{name}.png"
        _write_stage_image(path, pixels, ".png")
        item = {"id": f"seal-{index}:{name}", "name": name, "label": label,
                "image_url": f"{prefix.rstrip('/')}/{path.name}" if prefix else "",
                "channel": channel, "ocr_input": channel is not None}
        inputs.append(item)
        return item, path

    reference, _ = save("pale-grid-reference", measured["image"], "矩形章原图校平 · 保留手写内容")
    body, _ = save("pale-grid-enhanced", enhanced, "淡色红章增强 · 逐行识别来源")
    overlay = measured["image"].copy()
    rows, urls = [], []
    for number, (left, top, right, bottom) in enumerate(measured["row_boxes"]):
        crop = cv2.copyMakeBorder(enhanced[top:bottom, left:right], 20, 20, 20, 20,
                                  cv2.BORDER_CONSTANT, value=(255, 255, 255))
        item, path = save(f"rectangular-row-{number}", crop, "淡色矩形章整行 · OCR 实际输入", "body")
        item.update(read_mode="line", rectangle_row_index=number)
        rows.append({"crop_box": [left, top, right, bottom], "coordinate_space": "oriented",
                     "padding": 20, "path": str(path)})
        urls.append(item["image_url"])
        jobs.append((item, path))
        cv2.rectangle(overlay, (left, top), (right, bottom), (0, 140, 0), 2)
    frame, _ = save("pale-grid-frame", overlay, "完整矩形格线与逐行取样范围")
    metadata = {
        "shape": "矩形", "shape_code": "rectangle", "rectangular_layout": "frame_rows",
        "color_isolated_oriented_url": body["image_url"], "body_input_id": body["id"],
        "body_url": body["image_url"], "rectangular_frame_url": frame["image_url"],
        "rectangular_fixed_rows": rows, "rectangular_row_urls": urls,
        "rectangular_frame": {"crop_box": measured["frame_box"],
                              "method": "complete_pale_receiving_grid", **measured["metrics"]},
        "pale_grid_enhancement": {"method": "verified_grid_continuous_source_chroma",
                                  "gain": 6, "maximum_strength": 235, **statistics,
                                  "reference_url": reference["image_url"],
                                  "source_size": measured["source_size"],
                                  "source_to_rotated_matrix": measured["source_to_rotated_matrix"]},
        "orientation": {"mode": mode, "method": "complete_pale_receiving_grid",
                        "status": "按完整矩形格线校平，保留全部文字行",
                        "applied_rotation": measured["angle"], "rotation_steps": [measured["angle"]]},
    }
    return metadata, inputs, jobs
