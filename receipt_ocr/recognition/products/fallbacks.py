"""Image/OCR adapters for product table recovery and fusion."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from PIL import Image

from ...domain.ocr import TextObservation
from ...domain.parsing import LOW_CONFIDENCE_THRESHOLD, normalize_text, parse_product_table
from ...providers.text import TextRecognizer, default_text_recognizer


def _recover_missing_product_grades(
    source: Path,
    page_rows: list[TextObservation],
    product_table: dict,
    ocr_backend: str,
    text_recognizer: TextRecognizer | None = None,
) -> dict:
    """Retry a narrow enlarged table crop when the one-letter grade vanished."""
    missing_indexes = [
        index
        for index, row in enumerate(product_table.get("rows", []))
        if (
            not str(row.get("values", {}).get("等级", "")).strip()
            or not re.fullmatch(
                r"[A-D]", str(row.get("values", {}).get("等级", "")).strip()
            )
            or float(row.get("confidences", {}).get("等级", 0))
            < LOW_CONFIDENCE_THRESHOLD
        )
    ]
    if not missing_indexes:
        return product_table
    header = next(
        (row for row in page_rows if "行号" in row.text and 0.30 <= row.y <= 0.55),
        None,
    )
    if header is None:
        return product_table
    signature = next(
        (row for row in page_rows if "签章要求" in row.text and row.y > header.y), None
    )
    left, right = 0.39, 0.58
    top = max(0.0, header.y - 0.018)
    row_numbers = {
        normalize_text(str(row.get("values", {}).get("行号", "")))
        for row in product_table.get("rows", [])
    }
    row_anchors = [
        row
        for row in page_rows
        if row.x < 0.115
        and normalize_text(row.text) in row_numbers
        and row.y > header.y
    ]
    table_bottom = max(
        (row.y + row.height + 0.006 for row in row_anchors),
        default=header.y + 0.10,
    )
    bottom_limit = signature.y - 0.008 if signature else 0.95
    bottom = min(max(table_bottom, min(header.y + 0.10, bottom_limit)), bottom_limit)
    if bottom <= top + 0.025:
        return product_table

    recognizer = text_recognizer or default_text_recognizer()
    with Image.open(source) as image, tempfile.TemporaryDirectory(
        prefix="receipt-grade-"
    ) as temp_dir:
        width, height = image.size
        crop = image.crop(
            (
                int(left * width),
                int(top * height),
                int(right * width),
                int(bottom * height),
            )
        )
        crop = crop.resize((crop.width * 5, crop.height * 5), Image.Resampling.BICUBIC)
        crop_path = Path(temp_dir) / "grade-crop.png"
        crop.save(crop_path)
        try:
            crop_rows = recognizer.recognize(crop_path, backend=ocr_backend)
        except Exception:
            crop_rows = []

        # Detection may merge a nearby material suffix and the one-letter
        # grade. Retry each missing cell using its row-number Y coordinate and
        # Paddle's recognition-only model on the exact grade column.
        recovered_by_index: dict[int, tuple[str, float]] = {}
        for index in missing_indexes:
            detail = product_table["rows"][index]
            row_number = normalize_text(str(detail.get("values", {}).get("行号", "")))
            row_anchor = next(
                (
                    row
                    for row in page_rows
                    if row.x < 0.115
                    and normalize_text(row.text) == row_number
                    and header.y < row.y < bottom
                ),
                None,
            )
            if row_anchor is None:
                continue
            grade_left, grade_right = 0.43, 0.49
            grade_top = max(top, row_anchor.y + 0.001)
            grade_bottom = min(bottom, row_anchor.y + row_anchor.height + 0.003)
            grade_crop = image.crop(
                (
                    int(grade_left * width),
                    int(grade_top * height),
                    int(grade_right * width),
                    int(grade_bottom * height),
                )
            )
            grade_crop = grade_crop.resize(
                (max(1, grade_crop.width * 5), max(1, grade_crop.height * 5)),
                Image.Resampling.BICUBIC,
            )
            grade_path = Path(temp_dir) / f"grade-row-{index}.png"
            grade_crop.save(grade_path)
            try:
                line_rows = recognizer.recognize_line(grade_path, backend=ocr_backend)
            except Exception:
                line_rows = []
            for row in line_rows:
                cleaned = re.sub(r"[^A-Z]", "", row.text.upper())
                if re.fullmatch(r"[A-D]", cleaned) and row.confidence >= 0.60:
                    recovered_by_index[index] = (cleaned, float(row.confidence))
                    crop_rows.append(
                        TextObservation(
                            cleaned,
                            row.confidence,
                            (0.43 - left) / (right - left),
                            (grade_top - top) / (bottom - top),
                            (0.49 - 0.43) / (right - left),
                            max(0.001, (grade_bottom - grade_top) / (bottom - top)),
                        )
                    )
                    break

    recovered: list[TextObservation] = []
    for row in crop_rows:
        cleaned = re.sub(r"[^A-Z0-9]", "", row.text.upper())
        global_x = left + row.x * (right - left)
        global_width = row.width * (right - left)
        center_x = global_x + global_width / 2
        if not re.fullmatch(r"[A-Z]", cleaned) or not 0.43 <= center_x < 0.49:
            continue
        recovered.append(
            TextObservation(
                cleaned,
                row.confidence,
                global_x,
                top + row.y * (bottom - top),
                global_width,
                row.height * (bottom - top),
            )
        )
    if not recovered:
        return product_table

    retried = parse_product_table(page_rows + recovered)
    if len(retried.get("rows", [])) != len(product_table.get("rows", [])):
        return product_table
    for index in missing_indexes:
        old = product_table["rows"][index]
        new = retried["rows"][index]
        old_grade = str(old.get("values", {}).get("等级", "")).strip()
        direct_grade = recovered_by_index.get(index)
        grade = (
            direct_grade[0]
            if direct_grade
            else str(new.get("values", {}).get("等级", "")).strip()
        )
        if not re.fullmatch(r"[A-D]", grade):
            continue
        old["values"]["等级"] = grade
        material = str(old.get("values", {}).get("物料编号", ""))
        if re.search(r"[\u4e00-\u9fff]" + re.escape(grade) + r"$", material):
            old["values"]["物料编号"] = material[: -len(grade)]
            old.setdefault("sources", {})["物料编号"] = "OCR + 独立等级单元格拆分"
        old.setdefault("original_values", {}).setdefault("等级", old_grade)
        old.setdefault("confidences", {})["等级"] = (
            direct_grade[1]
            if direct_grade
            else new.get("confidences", {}).get("等级", 0.0)
        )
        old.setdefault("sources", {})["等级"] = f"{ocr_backend} 商品等级局部放大 OCR"
        old["low_confidence_columns"] = [
            name for name in old.get("low_confidence_columns", []) if name != "等级"
        ]
    return product_table
