"""Pure product description fusion rules."""

import re
from ..parsing import LOW_CONFIDENCE_THRESHOLD


def _fuse_product_material(primary: str, detail: str) -> str:
    """Keep the page model's code while taking a fuller detail-page description."""
    pattern = re.compile(
        r"^(?P<code>[A-Z0-9/\-]+)(?P<description>[\u4e00-\u9fff]+)"
        r"\s*(?P<capacity>\d+(?:G|TB))$",
        re.IGNORECASE,
    )
    primary_match = pattern.fullmatch("".join(str(primary).split()))
    detail_match = pattern.fullmatch("".join(str(detail).split()))
    if not primary_match or not detail_match:
        return ""
    if (
        primary_match.group("capacity").upper()
        != detail_match.group("capacity").upper()
    ):
        return ""
    primary_description = primary_match.group("description")
    detail_description = detail_match.group("description")
    if (
        len(detail_description) <= len(primary_description)
        or len(detail_description) > len(primary_description) + 2
    ):
        return ""
    if primary_description not in detail_description:
        return ""
    return (
        primary_match.group("code").upper()
        + detail_description
        + primary_match.group("capacity").upper()
    )

def _fuse_product_descriptions(primary_table: dict, detail_table: dict) -> None:
    """Fuse same-EAN product descriptions and retain both engines' evidence."""
    detail_by_ean = {
        str(row.get("values", {}).get("EAN码", "")).replace(" ", ""): row
        for row in detail_table.get("rows", [])
        if row.get("values", {}).get("EAN码")
    }
    changed = False
    for row in primary_table.get("rows", []):
        values = row.get("values", {})
        ean = str(values.get("EAN码", "")).replace(" ", "")
        detail_row = detail_by_ean.get(ean)
        if not detail_row:
            continue
        original = str(values.get("物料编号", ""))
        detail_value = str(detail_row.get("values", {}).get("物料编号", ""))
        fused = _fuse_product_material(original, detail_value)
        if not fused or fused == original:
            continue
        row.setdefault("original_values", {}).setdefault("物料编号", original)
        row["original_values"]["物料编号_整页回退"] = detail_value
        values["物料编号"] = fused
        primary_confidence = float(row.get("confidences", {}).get("物料编号", 0))
        detail_confidence = float(detail_row.get("confidences", {}).get("物料编号", 0))
        confidence = round(min(primary_confidence, detail_confidence), 3)
        row.setdefault("confidences", {})["物料编号"] = confidence
        row.setdefault("sources", {})["物料编号"] = "Paddle编码 + 整页商品描述补全"
        low_columns = set(row.get("low_confidence_columns", []))
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            low_columns.add("物料编号")
        row["low_confidence_columns"] = [
            name for name in row.get("values", {}) if name in low_columns
        ]
        required_scores = [
            float(row.get("confidences", {}).get(name, 0))
            for name in (
                "行号",
                "产品类别",
                "物料编号",
                "出库仓库",
                "数量",
                "重量",
                "体积",
                "EAN码",
            )
        ]
        row["row_confidence"] = round(sum(required_scores) / len(required_scores), 3)
        changed = True
    if changed:
        scores = [
            float(row.get("confidences", {}).get(name, 0))
            for row in primary_table.get("rows", [])
            for name in (
                "行号",
                "产品类别",
                "物料编号",
                "出库仓库",
                "数量",
                "重量",
                "体积",
                "EAN码",
            )
        ]
        primary_table["confidence"] = round(sum(scores) / len(scores), 3)
        primary_table["source"] += " + 同EAN跨引擎描述补全"
