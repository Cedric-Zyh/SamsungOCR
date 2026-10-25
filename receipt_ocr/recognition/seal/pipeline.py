"""One stamp -> prepared inputs -> OCR observations and ring ordering.

The requirement selects among actual OCR candidates and circular seam orders.
It never supplies missing characters or rewrites raw observations.
"""
from contextlib import nullcontext
from pathlib import Path
import tempfile

from PIL import Image, ImageOps

from receipt_ocr.runtime.execution import timed
from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.seal_serial import serial_matching_texts
from .preprocess.inputs import prepare_inputs
from .preprocess.number import merge_number_read
from .ocr.interface import OcrReader
from .postprocess.ring_text import reorder_ring_text
from .preprocess.ring_serial import read_ring_serial
from .preprocess.ring_alternative import read_normal_alternative
from .preprocess.continuous_company import read_continuous_company
from .preprocess.double_border import read_double_border
from .postprocess.multi_zone import finalize_multi_zone
from .postprocess.rectangle_form import mark_receiving_form_fields
from .postprocess.type_row import prefer_complete_type_read
from .postprocess.ring_alternative import select_ring_alternative


_RECTANGULAR_COMBINE_MIN_CONFIDENCE = 0.75
_RECTANGULAR_ROW_CENTER_TOLERANCE = 0.45
_RECTANGULAR_ROW_MAX_GAP = 0.04


def _ordered_rectangular_body_rows(observations):
    rows = [
        row for row in observations
        if getattr(row, "text", "")
        and float(getattr(row, "confidence", 0.0)) >= _RECTANGULAR_COMBINE_MIN_CONFIDENCE
    ]
    return sorted(rows, key=lambda row: (float(getattr(row, "y", 0.0)),
                                         float(getattr(row, "x", 0.0))))


def _row_edges(row):
    x = float(getattr(row, "x", 0.0))
    y = float(getattr(row, "y", 0.0))
    width = max(0.0, float(getattr(row, "width", 0.0)))
    height = max(0.0, float(getattr(row, "height", 0.0)))
    return x, y, x + width, y + height


def _horizontally_connected(left, right):
    lx1, _, lx2, _ = _row_edges(left)
    rx1, _, rx2, _ = _row_edges(right)
    overlap = min(lx2, rx2) - max(lx1, rx1)
    if overlap >= 0.0:
        return True
    return max(lx1, rx1) - min(lx2, rx2) <= _RECTANGULAR_ROW_MAX_GAP


def _rectangular_body_row_groups(observations):
    """Group rectangular OCR boxes into visual text rows.

    Horizontal overlap alone is unsafe because Paddle may return one
    oversized number-row box spanning the full stamp width.  Require the
    vertical centres to be close first, then use horizontal overlap or a
    small gap to connect neighbouring boxes on that same row.
    """
    rows = _ordered_rectangular_body_rows(observations)
    groups = []
    for row in rows:
        _, y1, _, y2 = _row_edges(row)
        center = (y1 + y2) / 2.0
        height = max(0.001, y2 - y1)
        placed = False
        for group in groups:
            centers = group["centers"]
            heights = group["heights"]
            group_center = sum(centers) / len(centers)
            tolerance = _RECTANGULAR_ROW_CENTER_TOLERANCE * max(
                height, sum(heights) / len(heights)
            )
            if abs(center - group_center) <= tolerance and any(
                _horizontally_connected(row, existing)
                for existing in group["rows"]
            ):
                group["rows"].append(row)
                group["centers"].append(center)
                group["heights"].append(height)
                placed = True
                break
        if not placed:
            groups.append({"rows": [row], "centers": [center], "heights": [height]})
    return [sorted(group["rows"], key=lambda row: float(getattr(row, "x", 0.0)))
            for group in sorted(groups, key=lambda item: sum(item["centers"]) / len(item["centers"]))]


def _read_rectangular_row_again(rows, body_path, reader, provider, directory, index, row_index,
                                horizontal_rows=None):
    """Re-read one merged rectangular text row from a padded crop."""
    if not body_path or not Path(body_path).is_file() or not rows:
        return []
    try:
        with Image.open(body_path) as image:
            image = image.convert("RGB")
            width, height = image.size
            # If the detector split a row into multiple boxes, use exactly
            # their outer bounds.  With only one box, borrow the horizontal
            # extent of the other stamp row(s) so a faint trailing character
            # outside the detector box is still available to OCR.
            x_rows = horizontal_rows if horizontal_rows else rows
            x1 = min(_row_edges(row)[0] for row in x_rows)
            y1 = min(_row_edges(row)[1] for row in rows)
            x2 = max(_row_edges(row)[2] for row in x_rows)
            y2 = max(_row_edges(row)[3] for row in rows)
            pad_x = max(0.02, (x2 - x1) * 0.04)
            pad_y = max(0.04, (y2 - y1) * 0.10)
            left = max(0, round((x1 - pad_x) * width))
            top = max(0, round((y1 - pad_y) * height))
            right = min(width, round((x2 + pad_x) * width))
            bottom = min(height, round((y2 + pad_y) * height))
            if right <= left or bottom <= top:
                return []
            crop = ImageOps.expand(image.crop((left, top, right, bottom)),
                                   border=(20, 20, 20, 20), fill="white")
            destination = Path(directory) / f"seal-{index}-rectangular-row-{row_index}.png"
            crop.save(destination)
        # The regular detector/recognizer sees the complete merged crop more
        # reliably than line recognition on the low-contrast stamp ink.
        reread = reader.read(destination, provider=provider)
        if reread:
            return reread
        return reader.read_line(destination, provider=provider)
    except Exception:
        return []


def _rectangular_body_text_parts(observations, *, body_path=None, reader=None,
                                 provider=None, directory=None, index=0):
    parts = []
    groups = _rectangular_body_row_groups(observations)
    all_rows = [row for group in groups for row in group]
    for row_index, group in enumerate(groups):
        # Multiple boxes in this row: use only this row's outer bounds.  A
        # single box: expand horizontally to the stamp's observed body span.
        horizontal_rows = group if len(group) > 1 else all_rows
        reread = (
            _read_rectangular_row_again(
                group, body_path, reader, provider, directory, index, row_index,
                horizontal_rows=horizontal_rows,
            )
            if reader is not None and directory is not None
            else []
        )
        reread_text = "".join(
            str(row.text).strip()
            for row in sorted(reread, key=lambda item: float(getattr(item, "x", 0.0)))
            if str(getattr(row, "text", "")).strip()
            and float(getattr(row, "confidence", 0.0)) >= _RECTANGULAR_COMBINE_MIN_CONFIDENCE
        )
        if reread_text:
            parts.append(reread_text)
        else:
            parts.append("".join(str(row.text).strip() for row in group))
    return parts


def _combine_rectangular_body_text(observations, **kwargs):
    """Combine one rectangular stamp's OCR rows in visual reading order.

    Rectangular stamps commonly contain a name row and a number row.  Paddle
    returns those as separate observations, but the strict seal matcher needs
    one candidate for the complete stamp.  Keep the original observations for
    audit and add this combined candidate to the matching text list.
    """
    # Keep weak fragments as raw evidence, but do not let a tiny background
    # fragment (for example ``nelah`` at confidence 0.42) get inserted into
    # an otherwise complete company-name + number candidate.
    rows = _ordered_rectangular_body_rows(observations)
    if len(rows) < 2:
        return ""
    return "".join(_rectangular_body_text_parts(observations, **kwargs))


@timed("local_seal_recognition")
def _recognize_local_seals(source, rows, regions, artifact_dir, artifact_url_prefix,
                           ocr_backend, secondary_ocr_backend=None, requirement="",
                           footer_anchor_y=None, orientation_mode="polygon"):
    reader = OcrReader()
    context = (nullcontext(str(Path(artifact_dir) / "seals")) if artifact_dir
               else tempfile.TemporaryDirectory(prefix="receipt-seals-"))
    prefix = f"{artifact_url_prefix.rstrip('/')}/seals" if artifact_url_prefix else ""
    texts, artifacts = [], []
    with context as directory:
        for index, region in enumerate(regions):
            region_text_start = len(texts)
            try:
                artifact, jobs = prepare_inputs(Path(source), region, index, directory, prefix,
                                               ocr_backend, orientation_mode)
            except Exception as exc:
                artifacts.append({"schema_version": 2, "index": index, "region_id": f"seal-{index}",
                                  "color": region.color, "inputs": [], "reads": [],
                                  "errors": [{"step": "prepare", "message": str(exc)}]})
                continue
            body_observations = []
            body_path = None
            for input_image, path in jobs:
                try:
                    # A ring is one revolution, not three alternative rows.
                    if "detected_rows" in input_image:
                        observations = [
                            TextObservation(row["text"], row["confidence"], row["x"], row["y"],
                                            row["width"], row["height"])
                            for row in input_image["detected_rows"]
                        ]
                    elif input_image["channel"] == "body" and input_image.get("read_mode") != "line":
                        observations = reader.read(path, provider=ocr_backend)
                    else:
                        observations = reader.read_line(path, provider=ocr_backend)
                    if input_image["channel"] == "center":
                        observations = prefer_complete_type_read(observations, input_image, artifact)
                    if artifact.get("shape_code") == "rectangle" and input_image["channel"] == "body":
                        body_observations.extend(observations)
                        body_path = path
                    input_image["status"] = "completed" if observations else "empty"
                    for position, row in enumerate(observations):
                        if not row.text:
                            continue
                        read = {"id": f"{input_image['id']}:{position}", "input_id": input_image["id"],
                                "region_id": artifact["region_id"], "channel": input_image["channel"],
                                "provider": ocr_backend, "text": row.text, "confidence": row.confidence,
                                "image_url": input_image.get("selected_read_image_url", input_image["image_url"])}
                        if artifact.get("rectangular_layout") in {"single_detection", "frame_rows", "frame_columns"}:
                            read["rectangle_row_index"] = (
                                position if "detected_rows" in input_image
                                else input_image["rectangle_row_index"]
                            )
                        artifact["reads"].append(read)
                        if input_image["channel"] not in {"layout_inner", "layout_lower"}:
                            texts.append(row.text)
                        channel_texts = [
                            item["text"] for item in artifact["reads"]
                            if item.get("channel") == input_image["channel"]
                        ]
                        if input_image["channel"] == "ring":
                            artifact["unwrap_texts"] = channel_texts
                            artifact["unwrapped_text"] = " | ".join(channel_texts)
                        elif input_image["channel"] == "center":
                            artifact["round_type_band_texts"] = channel_texts
                            artifact["round_type_band_text"] = " | ".join(channel_texts)
                        elif input_image["channel"] == "body":
                            artifact["color_isolated_texts"] = channel_texts
                            artifact["color_isolated_text"] = " | ".join(channel_texts)
                except Exception as exc:
                    input_image.update(status="failed", error=str(exc))
                    artifact["errors"].append({"step": input_image["channel"], "message": str(exc)})
            try:
                if read_double_border(artifact, directory, index, prefix,
                                      reader=reader, provider=ocr_backend):
                    # Replace only this stamp's mixed ring; keep its purpose
                    # and number channels. The website stays independent.
                    texts[region_text_start:] = [row['text'] for row in artifact['reads']
                                                  if row.get('channel') in {'ring', 'center', 'number', 'body'}]
            except Exception as exc:
                artifact['errors'].append({'step': 'double_border', 'message': str(exc)})
            try:
                if read_continuous_company(artifact, directory, index, prefix,
                                           reader=reader, provider=ocr_backend):
                    # Split readings remain diagnostic. Only this physical
                    # stamp's new active ring can enter the local comparison.
                    texts[region_text_start:] = [row['text'] for row in artifact['reads']
                                                  if row.get('channel') == 'ring']
            except Exception as exc:
                artifact['errors'].append({'step': 'continuous_company', 'message': str(exc)})
            texts.extend(finalize_multi_zone(artifact))
            merge_number_read(artifact)
            if artifact.get("stamp_number_status") == "merged":
                texts.append(artifact["round_type_band_text"])
            if artifact.get("shape_code") == "rectangle":
                mark_receiving_form_fields(artifact)
                fixed_layout = artifact.get("rectangular_layout") in {"single_detection", "frame_rows", "frame_columns"}
                if fixed_layout:
                    # Form values remain raw evidence, never stamp-name
                    # candidates, even when the receiving/date cell is filled.
                    texts[region_text_start:] = [read['text'] for read in artifact['reads']
                                                  if read.get('matching') is not False]
                    body_parts = ["" for _ in artifact["rectangular_fixed_rows"]]
                    for read in artifact["reads"]:
                        if (read.get("channel") == "body" and read["confidence"] >= .75
                                and read.get("matching") is not False):
                            body_parts[read["rectangle_row_index"]] += read["text"]
                    artifact["rectangular_row_texts"] = body_parts
                else:
                    body_parts = _rectangular_body_text_parts(
                        body_observations,
                        body_path=body_path,
                        reader=reader,
                        provider=ocr_backend,
                        directory=directory,
                        index=index,
                    )
                combined = "".join(body_parts)
                if combined:
                    artifact["rectangular_text"] = combined
                    artifact["rectangular_texts"] = body_parts
                    artifact["rectangular_row_texts"] = body_parts
                    if not fixed_layout:
                        row_images = sorted(
                            Path(directory).glob(f"seal-{index}-rectangular-row-*.png"),
                            key=lambda path: path.name,
                        )
                        artifact["rectangular_row_urls"] = [
                            f"{prefix}/{path.name}" for path in row_images
                        ]
                    texts.append(combined)
            ring_reads = [
                item["text"] for item in artifact["reads"]
                if item.get("channel") == "ring" and item.get("text")
            ]
            if ring_reads and not artifact.get("multi_zone_layout"):
                raw_ring_text = " | ".join(ring_reads)
                reorder = reorder_ring_text(
                    ring_reads[0], requirement=requirement, alternatives=ring_reads[1:]
                )
                artifact["unwrapped_raw_text"] = raw_ring_text
                artifact["ring_reorder"] = reorder
                artifact["unwrapped_text"] = reorder["text"]
                if reorder["changed"]:
                    texts.append(reorder["text"])
            if artifact.get("shape_code") == "round" and not artifact.get("multi_zone_layout"):
                # Independent evidence; included in matching only if requested.
                serial_source = Path(directory) / f"seal-{index}-ring-input.png"
                if serial_source.is_file():
                    try:
                        read_ring_serial(serial_source, directory, index, prefix, artifact,
                                         reader=reader, provider=ocr_backend)
                    except Exception as exc:
                        artifact["ring_serial_detection"] = {"status": "failed"}
                        artifact["errors"].append({"step": "ring_serial", "message": str(exc)})
            texts.extend(serial_matching_texts(requirement, artifact))
            if (artifact.get("shape_code") == "ellipse" and not artifact.get("multi_zone_layout")
                    and not artifact.get("double_border_layout")):
                # A complete observed alternative can beat a wrong primary
                # reading, even when the primary's confidence is higher.
                try:
                    artifact["ring_normal_alternative"] = read_normal_alternative(
                        directory, index, prefix, artifact, reader=reader, provider=ocr_backend)
                    texts.extend(select_ring_alternative(artifact, requirement))
                except Exception as exc:
                    artifact["ring_normal_alternative"] = {
                        "status": "failed", "matching": False, "error": str(exc)}
            artifacts.append(artifact)
    return texts, artifacts
