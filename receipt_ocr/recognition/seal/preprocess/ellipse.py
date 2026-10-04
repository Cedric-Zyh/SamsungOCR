"""Stamp orientation: ellipse."""

from pathlib import Path
import re
import tempfile
import numpy as np
from PIL import Image
from receipt_ocr.recognition.seal.preprocess.orientation_angles import _round_stamp_type_texts, choose_round_stamp_angle
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _oriented_type_row_box, _oriented_type_row_exact_box, _refine_ellipse_axis, rotate_stamp_image
from receipt_ocr.recognition.seal.preprocess.round import prepare_round_stamp


def prepare_ellipse_stamp(source, destination, *, model_variant="v6"):
    """Orient an oval stamp with its centre line, including quarter-turns.

    Ellipse geometry can estimate tilt but cannot tell which end is upright.
    Reuse the normal text-polygon route first; when the detector has no
    trustworthy anchor, compare the centre-line reading at all four right
    angle orientations.  This also handles an oval whose long axis is
    vertical, where a horizontal centre crop otherwise sees only one glyph.
    """
    oriented, decision = prepare_round_stamp(
        source, destination, model_variant=model_variant
    )
    decision["shape_route"] = "ellipse"
    if oriented is not None or decision.get("anchor_text"):
        # The polygon route may already have selected a quarter-turn, but an
        # oval captured with perspective can still leave the centre row
        # slanted. Always refine that successful route before returning; the
        # fallback below is only responsible for choosing a missing direction.
        base = oriented or Path(source)
        try:
            from receipt_ocr.recognition.seal.ocr.interface import detect_boxes

            fine_boxes = detect_boxes(base, model_variant=model_variant)
            fine_decision = choose_round_stamp_angle(
                fine_boxes, minimum_confidence=0.45
            )
            fine_rotation = float(fine_decision.get("angle") or 0.0)
            if abs(fine_rotation) > 2.0:
                oriented = rotate_stamp_image(base, destination, fine_rotation)
                decision["fine_rotation"] = round(fine_rotation, 3)
                decision["status"] = (
                    f"{decision.get('status', '已按印章文字检测框旋正')}，"
                    f"再微调 {fine_rotation:.1f}°"
                )
                decision["oriented_path"] = str(oriented)
            geometry_source = oriented or base
            geometry_path, geometry_rotation = _refine_ellipse_axis(
                geometry_source, destination
            )
            if abs(geometry_rotation) > 2.0:
                oriented = geometry_path
                decision["geometry_rotation"] = round(geometry_rotation, 3)
                decision["oriented_path"] = str(oriented)
                decision["status"] = (
                    f"{decision.get('status', '已按印章文字方向旋正')}，"
                    f"按椭圆长轴再校正 {geometry_rotation:.1f}°"
                )
            # Geometry refinement changes the pixel frame after the original
            # detector boxes were recorded.  Re-detect on the final image so
            # the later type-row fill is aligned with the pixels that reach
            # ring OCR.
            final_path = oriented or base
            final_boxes = detect_boxes(final_path, model_variant=model_variant)
            final_decision = choose_round_stamp_angle(
                final_boxes, minimum_confidence=0.45
            )
            with Image.open(final_path) as image:
                final_width, final_height = image.size
            identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            final_focus = _oriented_type_row_box(
                final_boxes, final_decision, identity, final_width, final_height
            )
            final_exact = _oriented_type_row_exact_box(
                final_boxes, final_decision, identity, final_width, final_height
            )
            if final_focus and final_exact:
                decision["detected_boxes"] = final_boxes
                decision["anchor_points"] = final_decision.get("anchor_points", [])
                decision["type_row_texts"] = _round_stamp_type_texts(
                    final_boxes, final_decision
                )
                if oriented is not None:
                    decision["oriented_type_row_box"] = final_focus
                    decision["oriented_type_row_exact_box"] = final_exact
                else:
                    decision["type_row_box"] = final_focus
                    decision["type_row_exact_box"] = final_exact
        except Exception:
            pass
        return oriented, decision

    from receipt_ocr.recognition.seal.ocr.interface import read_line

    def _score(rows):
        values = [row for row in rows if getattr(row, "text", "")]
        if not values:
            return 0.0, ""
        text = "".join(str(row.text) for row in values)
        confidence = sum(float(getattr(row, "confidence", 0.0) or 0.0)
                         for row in values) / len(values)
        # A direction decision must come from the centre stamp-type row, not
        # a company fragment on the ellipse ring.  If no type marker is read,
        # return no evidence and leave the crop in its current orientation.
        is_type_text = (
            ("收货" in text and "章" in text)
            or ("专用" in text and "章" in text)
            or "代码" in text
            or bool(re.fullmatch(r"[（(]?\s*\d{1,3}\s*[）)]?", text))
        )
        if not is_type_text:
            return 0.0, ""
        marker_bonus = 0.35 if ("收货" in text and "章" in text) else 0.0
        marker_bonus += 0.20 if ("专用" in text and "章" in text) else 0.0
        return confidence + marker_bonus, text

    try:
        with Image.open(source) as image:
            rgb = image.convert("RGB")
        with tempfile.TemporaryDirectory(prefix="receipt-ellipse-ori-") as tmp:
            candidate_rows = {}
            for angle in (0, 90, 180, 270):
                candidate_path = Path(tmp) / f"center-{angle}.png"
                rotated = rgb.rotate(angle, expand=True, fillcolor="white")
                width, height = rotated.size
                left, right = round(width * 0.04), round(width * 0.96)
                # Crop after rotating the full stamp. Cropping before a
                # quarter-turn loses the top and bottom glyphs of a vertical
                # ``收货章`` row, leaving only one character for OCR.
                top, bottom = round(height * 0.38), round(height * 0.64)
                rotated.crop((left, top, right, bottom)).save(candidate_path)
                candidate_rows[angle] = read_line(
                    candidate_path, model_variant=model_variant
                )
        scored = {
            angle: _score(rows) for angle, rows in candidate_rows.items()
        }
        best_angle = max(
            scored,
            key=lambda angle: (scored[angle][0], angle in (0, 180), -angle),
        )
        best_score, best_text = scored[best_angle]
        decision["ellipse_orientation_candidates"] = {
            str(angle): {
                "text": text,
                "score": round(score, 4),
            }
            for angle, (score, text) in scored.items()
        }
        if best_score < 0.70:
            decision["status"] = "椭圆中心行未确认方向，保留原方向"
            decision["applied_rotation"] = 0.0
            return None, decision
        oriented = (
            rotate_stamp_image(source, destination, float(best_angle))
            if best_angle
            else Path(source)
        )
        fine_rotation = 0.0
        # A right-angle choice fixes a vertical oval, but camera perspective
        # can leave the centre row tilted by another few degrees. Detect the
        # now-readable type row once more and use its polygon angle for a
        # small corrective rotation.
        try:
            from receipt_ocr.recognition.seal.ocr.interface import detect_boxes

            fine_boxes = detect_boxes(oriented, model_variant=model_variant)
            fine_decision = choose_round_stamp_angle(
                fine_boxes, minimum_confidence=0.45
            )
            fine_rotation = float(fine_decision.get("angle") or 0.0)
            if abs(fine_rotation) > 2.0:
                oriented = rotate_stamp_image(oriented, destination, fine_rotation)
        except Exception:
            fine_rotation = 0.0
        try:
            geometry_path, geometry_rotation = _refine_ellipse_axis(
                oriented, destination
            )
            if abs(geometry_rotation) > 2.0:
                oriented = geometry_path
                decision["geometry_rotation"] = round(geometry_rotation, 3)
                decision["status"] = (
                    f"{decision.get('status', '按椭圆中心单行旋正')}，"
                    f"按椭圆长轴再校正 {geometry_rotation:.1f}°"
                )
        except Exception:
            geometry_rotation = 0.0
        if (
            best_angle == 0
            and abs(fine_rotation) <= 2.0
            and abs(geometry_rotation) <= 2.0
        ):
            decision.update(
                angle=0.0,
                applied_rotation=0.0,
                anchor_text=best_text,
                confidence=round(best_score, 4),
                status="椭圆中心单行已接近水平，保留原方向",
            )
            return None, decision
        decision.update(
            angle=float(best_angle),
            applied_rotation=float(best_angle),
            anchor_text=best_text,
            confidence=round(best_score, 4),
            fine_rotation=round(fine_rotation, 3),
            geometry_rotation=round(geometry_rotation, 3),
            oriented_path=str(oriented),
            status=(
                f"按椭圆中心单行识别结果旋转 {best_angle}°"
                + (f"，再微调 {fine_rotation:.1f}°" if abs(fine_rotation) > 2 else "")
            ),
        )
        return oriented, decision
    except Exception as exc:
        decision["ellipse_orientation_error"] = str(exc)
        decision["status"] = "椭圆中心行方向比较失败，保留原方向"
        decision["applied_rotation"] = 0.0
        return None, decision
