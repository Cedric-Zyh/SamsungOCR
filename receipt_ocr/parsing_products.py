"""Product-table geometry, value normalization and cell validation."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .document_types import continuation_row_evidence
from .ocr_types import TextObservation
from .parsing_constants import (
    LOW_CONFIDENCE_THRESHOLD,
    PRODUCT_COLUMNS,
    PRODUCT_COLUMN_RANGES,
    PRODUCT_REQUIRED_COLUMNS,
    VERIFIED_EAN_BY_MATERIAL,
    VERIFIED_MATERIAL_BY_EAN,
)
from .parsing_text import normalize_text


def parse_product_table(rows: list[TextObservation]) -> dict:
    """Extract product detail rows by geometric columns and Y-line clustering."""
    header = next((row for row in rows if normalize_text(row.text).startswith("行号")), None)
    continuation = continuation_row_evidence(rows)
    headerless = header is None and continuation["complete_rows"] >= 3
    if not header and not headerless:
        return {"columns": list(PRODUCT_COLUMNS), "rows": [], "confidence": 0.0, "source": "未定位表头"}

    header_y = header.y if header else max(0.0, float(continuation["first_y"]) - 0.004)
    if headerless:
        total = next((row for row in rows if row.y > header_y and "合计" in row.text), None)
        end_y = total.y - 0.002 if total else min(float(continuation["last_y"]) + 0.02, 0.72)
    else:
        total = next(
            (row for row in rows if row.y > header_y and row.y - header_y < 0.18 and "合计" in row.text),
            None,
        )
        if total:
            end_y = total.y - 0.002
        elif (
            continuation["complete_rows"] >= 10
            and float(continuation["last_y"]) > header_y + 0.13
        ):
            # Accessory orders can fill the whole cover page and continue on
            # ``_01`` without a total/footer on page one.  Extend only when at
            # least ten geometrically complete rows prove a real dense table;
            # ordinary one-line receipts retain the conservative fixed crop.
            end_y = min(float(continuation["last_y"]) + 0.02, 0.95)
        else:
            end_y = min(header_y + 0.13, 0.59)
    candidates = [
        row for row in rows
        if header_y + 0.003 <= row.y <= end_y
        and not any(token in row.text for token in ("签章要求", "签收说明", "合计"))
        and re.search(r"[0-9A-Za-z\u4e00-\u9fff]", row.text)
    ]

    total_cells = _product_values_by_columns(
        [row for row in rows if total and abs((row.y + row.height / 2) - (total.y + total.height / 2)) <= 0.009]
    ) if total else {name: "" for name in PRODUCT_COLUMNS}

    skew = _estimate_product_skew(rows, header_y)

    def deskewed_center_y(item: TextObservation) -> float:
        return item.y + item.height / 2 - skew * (item.x + item.width / 2)

    clusters: list[list[TextObservation]] = []
    for row in sorted(candidates, key=lambda item: (deskewed_center_y(item), item.x)):
        center_y = deskewed_center_y(row)
        matching = next(
            (
                cluster for cluster in clusters
                if abs(
                    center_y
                    - sum(deskewed_center_y(item) for item in cluster) / len(cluster)
                ) <= max(0.0075, row.height * 0.75)
            ),
            None,
        )
        if matching is None:
            clusters.append([row])
        else:
            matching.append(row)

    output_rows = []
    for index, cluster in enumerate(clusters):
        cells: dict[str, list[TextObservation]] = {name: [] for name in PRODUCT_COLUMNS}
        split_columns: set[str] = set()
        for observation in cluster:
            assignments = _product_observation_assignments(observation)
            if len(assignments) > 1:
                split_columns.update(column for column, _ in assignments)
            for column, assigned in assignments:
                cells[column].append(assigned)

        original_values = _product_values_by_columns([item for items in cells.values() for item in items])
        values = {name: _normalize_product_value(name, value) for name, value in original_values.items()}
        sources = {
            name: (
                "OCR + 跨列拆分" if name in split_columns
                else "OCR + 代码规则" if values[name] != original_values[name]
                else "OCR"
            )
            for name in PRODUCT_COLUMNS
        }
        # The one-letter grade can touch the end of the fixed power-bank
        # description. Restrict this repair to ``mAh移动电源`` plus an empty
        # grade so genuine material codes ending in A remain unchanged.
        material = values["物料编号"]
        if not values["等级"] and re.search(r"mAh移动电A$", material, re.IGNORECASE):
            values["物料编号"] = material[:-1] + "源"
            values["等级"] = "A"
            sources["物料编号"] = "OCR + 固定商品描述校正"
            sources["等级"] = "OCR + 跨列拆分"
        catalog_material = VERIFIED_MATERIAL_BY_EAN.get(values["EAN码"])
        if (
            catalog_material
            and values["物料编号"] != catalog_material
            and SequenceMatcher(None, values["物料编号"], catalog_material).ratio() >= 0.75
        ):
            values["物料编号"] = catalog_material
            sources["物料编号"] = "EAN 校验商品目录校正"
        verified_ean = VERIFIED_EAN_BY_MATERIAL.get(values["物料编号"])
        if not values["EAN码"] and verified_ean and _valid_ean13(verified_ean):
            values["EAN码"] = verified_ean
            sources["EAN码"] = "精确物料号 + 已复核 EAN 商品目录回填"
        # A valid detail row must have a row number plus at least two business cells.
        if not re.fullmatch(r"\d{1,4}", values["行号"].replace(" ", "")):
            continue
        if sum(bool(values[name]) for name in PRODUCT_COLUMNS[1:]) < 2:
            continue

        confidence = {
            name: _product_cell_confidence(name, values[name], items)
            for name, items in cells.items()
        }
        if sources.get("等级") == "OCR + 跨列拆分" and values["等级"] and not cells["等级"]:
            confidence["等级"] = 0.86
        if sources.get("物料编号") == "EAN 校验商品目录校正":
            confidence["物料编号"] = max(confidence["物料编号"], 0.99)
        if sources.get("EAN码") == "精确物料号 + 已复核 EAN 商品目录回填":
            confidence["EAN码"] = 0.99
        output_rows.append({
            "index": index,
            "values": values,
            "original_values": original_values,
            "confidences": confidence,
            "sources": sources,
            "low_confidence_columns": [
                name for name in PRODUCT_COLUMNS
                if (name in PRODUCT_REQUIRED_COLUMNS and not values[name])
                or (values[name] and confidence[name] < LOW_CONFIDENCE_THRESHOLD)
            ],
            "row_confidence": round(
                sum(confidence[name] for name in PRODUCT_REQUIRED_COLUMNS)
                / len(PRODUCT_REQUIRED_COLUMNS),
                3,
            ),
            "source": "OCR 按列定位",
        })

    # 单条明细时，合计行的重量和体积就是该商品行。红章压住明细
    # 数字但合计数字仍清晰时，可用作独立交叉验证。
    if len(output_rows) == 1:
        detail = output_rows[0]
        for name in ("重量", "体积"):
            total_value = _normalize_product_value(name, total_cells.get(name, ""))
            if not detail["values"][name] and re.fullmatch(r"\d+(?:\.\d+)?", total_value):
                detail["values"][name] = total_value
                detail["original_values"][name] = ""
                detail["confidences"][name] = 0.88
                detail["sources"][name] = "合计行交叉验证"
                detail["low_confidence_columns"] = [
                    column for column in detail["low_confidence_columns"] if column != name
                ]

    # 单条明细时，“合计”数量等于该行数量，可作为一次独立交叉验证。
    # 除了完全漏识，也覆盖数量列被读成 ``pae`` 这类非数字文本；
    # 已有合法数字绝不覆盖，多行表也不使用合计回填。
    if (
        len(output_rows) == 1
        and not re.fullmatch(
            r"\d+(?:\.\d+)?", output_rows[0]["values"]["数量"]
        )
    ):
        total_quantity = re.sub(r"\s", "", total_cells.get("数量", ""))
        quantity_source = "合计行交叉验证"
        quantity_confidence = 0.88
        if not total_quantity:
            detail_values = output_rows[0]["values"]
            same_weight = _same_decimal(detail_values.get("重量", ""), total_cells.get("重量", ""))
            same_volume = _same_decimal(detail_values.get("体积", ""), total_cells.get("体积", ""))
            if same_weight and same_volume:
                total_quantity = "1"
                quantity_source = "合计重量/体积一致推导"
                quantity_confidence = 0.82
        if re.fullmatch(r"\d+(?:\.\d+)?", total_quantity):
            detail = output_rows[0]
            original_quantity = detail["original_values"].get("数量", "")
            detail["values"]["数量"] = total_quantity
            detail["original_values"]["数量"] = original_quantity
            detail["confidences"]["数量"] = quantity_confidence
            detail["sources"]["数量"] = quantity_source
            detail["low_confidence_columns"] = [name for name in detail["low_confidence_columns"] if name != "数量"]
            detail["row_confidence"] = round(
                sum(detail["confidences"][name] for name in PRODUCT_REQUIRED_COLUMNS)
                / len(PRODUCT_REQUIRED_COLUMNS),
                3,
            )

    # Standard Samsung cover receipts use SAP-style line numbering beginning
    # at 10.  A thin leading ``1`` can disappear into the left page/table
    # border, leaving a high-confidence ``0``.  Repair only the strongly
    # constrained single-row cover-page shape (real header + total, G1/W002,
    # quantity 1 and a full EAN); continuation or multi-row tables are never
    # changed by this rule.
    if len(output_rows) == 1 and header and total:
        detail = output_rows[0]
        values = detail["values"]
        if (
            values.get("行号") == "0"
            and values.get("产品类别") == "G1"
            and values.get("出库仓库") == "W002"
            and values.get("数量") == "1"
            and re.fullmatch(r"\d{13}", values.get("EAN码", ""))
        ):
            values["行号"] = "10"
            detail["sources"]["行号"] = "标准单行首页首行号结构校正"
            detail["confidences"]["行号"] = 0.96
            detail["low_confidence_columns"] = [
                name for name in detail["low_confidence_columns"] if name != "行号"
            ]

    if len(output_rows) == 1:
        detail = output_rows[0]
        detail["row_confidence"] = round(
            sum(detail["confidences"][name] for name in PRODUCT_REQUIRED_COLUMNS)
            / len(PRODUCT_REQUIRED_COLUMNS),
            3,
        )

    # Long accessory manifests often have one grade and one warehouse for the
    # whole table.  A customer stamp can erase a handful of cells while the
    # same column remains independently visible in dozens of other rows. Use
    # that column consensus only with strong support; never overwrite another
    # syntactically valid value, so mixed-grade/mixed-warehouse tables remain
    # untouched.
    if len(output_rows) >= 10:
        for column, valid_pattern in (
            ("等级", re.compile(r"[A-D]")),
            ("出库仓库", re.compile(r"[A-Z0-9]{3,6}")),
        ):
            observed = [
                str(detail["values"].get(column, "")).strip()
                for detail in output_rows
                if valid_pattern.fullmatch(str(detail["values"].get(column, "")).strip())
            ]
            if not observed:
                continue
            dominant = max(set(observed), key=observed.count)
            support = observed.count(dominant)
            if support < 8 or support / len(output_rows) < 0.85:
                continue
            for detail in output_rows:
                current = str(detail["values"].get(column, "")).strip()
                if valid_pattern.fullmatch(current):
                    continue
                detail["values"][column] = dominant
                detail.setdefault("sources", {})[column] = "同表列强一致性校正"
                detail.setdefault("confidences", {})[column] = 0.92
                detail["low_confidence_columns"] = [
                    name for name in detail.get("low_confidence_columns", [])
                    if name != column
                ]
                detail["row_confidence"] = round(
                    sum(detail["confidences"][name] for name in PRODUCT_REQUIRED_COLUMNS)
                    / len(PRODUCT_REQUIRED_COLUMNS),
                    3,
                )

    populated = [detail["confidences"][name] for detail in output_rows for name in PRODUCT_REQUIRED_COLUMNS]
    return {
        "columns": list(PRODUCT_COLUMNS),
        "rows": output_rows,
        "confidence": round(sum(populated) / len(populated), 3) if populated else 0.0,
        "source": (
            "无表头续页 + 固定列坐标/按行聚类"
            if headerless else "表头定位 + 文字框按列/按行聚类"
        ),
    }


def product_table_text(table: dict) -> str:
    return "\n".join(
        " | ".join(str(row.get("values", {}).get(name, "")) for name in PRODUCT_COLUMNS)
        for row in table.get("rows", [])
    )


def _cluster_center_y(cluster: list[TextObservation]) -> float:
    return sum(item.y + item.height / 2 for item in cluster) / max(1, len(cluster))


def _estimate_product_skew(rows: list[TextObservation], header_y: float) -> float:
    """Estimate the table baseline slope from geometrically separated headers."""
    headers = []
    normalized_columns = [normalize_text(name) for name in PRODUCT_COLUMNS]
    for row in rows:
        normalized = normalize_text(row.text)
        if abs(row.y - header_y) > 0.025:
            continue
        if any(name and name in normalized for name in normalized_columns):
            headers.append((row.x + row.width / 2, row.y + row.height / 2))
    if len(headers) < 3:
        return 0.0
    mean_x = sum(x for x, _ in headers) / len(headers)
    mean_y = sum(y for _, y in headers) / len(headers)
    denominator = sum((x - mean_x) ** 2 for x, _ in headers)
    if denominator <= 1e-9:
        return 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in headers) / denominator
    return max(-0.05, min(0.05, slope))


def _product_values_by_columns(rows: list[TextObservation]) -> dict[str, str]:
    cells: dict[str, list[TextObservation]] = {name: [] for name in PRODUCT_COLUMNS}
    for observation in rows:
        for column, assigned in _product_observation_assignments(observation):
            if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", assigned.text):
                cells[column].append(assigned)
    return {
        name: " ".join(item.text.strip() for item in sorted(items, key=lambda item: item.x) if item.text.strip())
        for name, items in cells.items()
    }


def _product_observation_assignments(
    observation: TextObservation,
) -> list[tuple[str, TextObservation]]:
    """Assign one OCR box to columns, splitting merged weight/volume decimals."""
    compact = re.sub(r"\s", "", observation.text)
    # Paddle occasionally merges a one-letter grade into a material box that
    # crosses the material/grade boundary (for example ``...512GA``).  Split
    # only when the material ends in a capacity token, which avoids trimming
    # legitimate material-code letters.
    material_grade = re.fullmatch(r"(.+(?:\d{2,4}G|\d+TB))([A-Z])", compact, re.IGNORECASE)
    material_grade_boundary = PRODUCT_COLUMN_RANGES["物料编号"][1]
    if (
        material_grade
        and observation.x < material_grade_boundary < observation.x + observation.width
    ):
        material, grade = material_grade.groups()
        left_width = max(0.001, material_grade_boundary - observation.x)
        right_width = max(0.001, observation.x + observation.width - material_grade_boundary)
        return [
            ("物料编号", TextObservation(
                material, observation.confidence, observation.x, observation.y,
                left_width, observation.height,
            )),
            ("等级", TextObservation(
                grade.upper(), observation.confidence, material_grade_boundary,
                observation.y, right_width, observation.height,
            )),
        ]
    decimal_pair = re.fullmatch(r"(0?\.\d{3})(0?\.\d{3})", compact)
    weight_volume_boundary = PRODUCT_COLUMN_RANGES["重量"][1]
    if (
        decimal_pair
        and observation.x < weight_volume_boundary < observation.x + observation.width
    ):
        first, second = decimal_pair.groups()
        left_width = max(0.001, weight_volume_boundary - observation.x)
        right_width = max(0.001, observation.x + observation.width - weight_volume_boundary)
        return [
            ("重量", TextObservation(
                first, observation.confidence, observation.x, observation.y,
                left_width, observation.height,
            )),
            ("体积", TextObservation(
                second, observation.confidence, weight_volume_boundary, observation.y,
                right_width, observation.height,
            )),
        ]

    center_x = observation.x + observation.width / 2
    column = next(
        (name for name, (left, right) in PRODUCT_COLUMN_RANGES.items() if left <= center_x < right),
        None,
    )
    return [(column, observation)] if column else []


def _normalize_product_value(name: str, value: str) -> str:
    compact = value.strip()
    if name in {"行号", "数量", "重量", "体积", "EAN码"}:
        # Paddle may split one printed decimal into adjacent boxes (``0.`` and
        # ``409``); column joining inserts a space that is not business data.
        compact = re.sub(r"\s+", "", compact)
    if name in {"重量", "体积"}:
        # A faint Chinese/table glyph can be attached before an otherwise
        # complete decimal (``可。1.286``). Recover it only when exactly one
        # numeric value remains after non-numeric edge noise; ambiguous cells
        # with multiple numbers stay untouched and low-confidence.
        numeric = re.fullmatch(r"\D*(\d+(?:\.\d+)?)\D*", compact)
        if numeric:
            compact = numeric.group(1)
    elif name == "EAN码":
        # Dense Server-model tables can attach a few Chinese glyphs from the
        # next column/page footer to an otherwise intact EAN.  Strip edge
        # noise only when there is exactly one 13-digit sequence and its
        # checksum is valid; malformed or ambiguous digit strings stay visible
        # for review.
        embedded_ean = re.fullmatch(r"\D*(\d{13})\D*", compact)
        if embedded_ean and _valid_ean13(embedded_ean.group(1)):
            compact = embedded_ean.group(1)
    if name in {"产品类别", "等级"}:
        compact = compact.upper().replace("I", "1").replace("L", "1")
        # Low-resolution table lines are occasionally returned as punctuation
        # attached to a one-letter grade (for example ``;A``).  Punctuation is
        # not a valid business value in either categorical column.
        compact = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", compact)
    elif name == "出库仓库":
        compact = compact.upper()
        if re.fullmatch(r"W[O0]\d{2}", compact):
            compact = "W0" + compact[2:]
    elif name == "物料编号":
        compact = re.sub(r"^(?:SW|SI)-", "SM-", compact, flags=re.IGNORECASE)
        # Samsung's reviewed material-code alphabet uses digit zero rather
        # than letter O.  PP-OCR Server systematically emits O in dense rows
        # (for example TOS926/CNFC and DX92O/EGCN).  Limit correction to a
        # leading structured SKU with a known Samsung market suffix; free-form
        # descriptions and unknown product families are left untouched.
        code_match = re.match(r"([A-Za-z]{1,3}-[A-Za-z0-9-]{6,})(.*)", compact)
        if code_match:
            code, description = code_match.groups()
            upper_code = code.upper()
            if (
                "O" in upper_code
                and any(char.isdigit() for char in upper_code)
                and upper_code.endswith(("CHC", "EGCN", "GCN", "CNFC", "YC"))
            ):
                compact = upper_code.replace("O", "0") + description
    return compact


def _same_decimal(left: str, right: str) -> bool:
    try:
        return bool(left and right) and float(left.replace(" ", "")) == float(right.replace(" ", ""))
    except ValueError:
        return False


def _product_cell_confidence(name: str, value: str, rows: list[TextObservation]) -> float:
    if not value or not rows:
        return 0.0
    total_chars = sum(max(1, len(row.text.strip())) for row in rows)
    raw = sum(float(row.confidence) * max(1, len(row.text.strip())) for row in rows) / total_chars
    compact = re.sub(r"\s", "", value)
    validated = False
    if name in {"行号", "数量"}:
        validated = bool(re.fullmatch(r"\d+(?:\.\d+)?", compact))
    elif name in {"重量", "体积"}:
        validated = bool(re.fullmatch(r"\d+(?:\.\s*\d+)?", compact))
    elif name == "EAN码":
        validated = bool(re.fullmatch(r"\d{13}", compact)) and _valid_ean13(compact)
    elif name == "出库仓库":
        validated = bool(re.fullmatch(r"[A-Z]{1,3}\d{1,4}", compact.upper()))
    elif name == "等级":
        validated = bool(re.fullmatch(r"[A-Z][A-Z0-9]?", compact.upper()))
    elif name == "产品类别":
        validated = bool(re.fullmatch(r"[A-Z]\d", compact.upper()))
    elif name == "物料编号":
        validated = len(compact) >= 7 and bool(re.search(r"[A-Za-z0-9]", compact))
    # Type/校验位通过时提升可靠度，但仍保留 OCR 的不确定性。
    if validated:
        floors = {
            "EAN码": 0.99,
            "行号": 0.9,
            "数量": 0.9,
            "重量": 0.9,
            "体积": 0.9,
            "出库仓库": 0.88,
            "等级": 0.85,
            "产品类别": 0.86,
            # 只验证了编码形态，不能证明其中每个易混淆字符都正确。
            "物料编号": 0.70,
        }
        raw = max(raw, floors.get(name, 0.75))
    return round(max(0.0, min(1.0, raw)), 3)


def _valid_ean13(value: str) -> bool:
    digits = [int(char) for char in value]
    check = (10 - (sum(digits[:12:2]) + 3 * sum(digits[1:12:2])) % 10) % 10
    return check == digits[-1]
