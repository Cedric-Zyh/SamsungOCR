"""Reuse the stamp boxes QingTong already located instead of detecting our own.

The seal API returns the pixel rectangle of every stamp it judged.  Detecting a
second guess of our own around the footer is both slower and, on faint or
overlapping stamps, less accurate than the box the API actually read.  When that
box exists it becomes the local region set, so the cropped evidence image and
the local OCR pass both describe the one stamp the API decided about instead of
two different ones.

``商品付讫章`` (the dispatch stamp) never reaches this module:
:func:`receipt_ocr.qingtong_seal.compare_qingtong_seal` moves it to
``excluded_candidates`` before a candidate list exists, so every box here is a
stamp that may carry the customer's receipt seal.
"""

from __future__ import annotations

from math import isfinite
from pathlib import Path

from .image_processing import (
    SealRegion,
    classify_seal_role,
    read_image_size,
    save_pixel_region_crop,
)

# The API does not report which ink its box was drawn from, and the colour only
# steers which derivative crops the local pass prepares.  "red" is the tolerant
# branch: it keeps the original crop and both colour masks available.
REMOTE_COLOR = "red"
REMOTE_SHAPE = "矩形"
REMOTE_SOURCE = "清瞳印章区域"

# The policies ``compare_qingtong_seal`` can emit.  Anything else means the
# check did not come from the seal API and carries no usable boxes.
REMOTE_POLICIES = frozenset(
    {"qingtong_template_and_ocr", "qingtong_any_channel", "qingtong_all_channel"}
)


def _valid_box(value) -> tuple[float, float, float, float] | None:
    """Accept only a finite ``xyxy`` with a positive extent."""
    if isinstance(value, (list, tuple)) and len(value) == 4:
        numbers = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            if not isfinite(item):
                return None
            numbers.append(float(item))
        x1, y1, x2, y2 = numbers
        if x2 > x1 and y2 > y1:
            return x1, y1, x2, y2
    return None


def qingtong_region_boxes(check) -> list[dict]:
    """Validated pixel boxes of the stamps QingTong offered as seal evidence.

    ``index`` counts only the boxes kept here, so downstream numbering starts at
    one; ``api_index`` keeps the position inside the API response so a box can
    still be traced back to the seal it came from.
    """
    dual = check.get("dual_check") if isinstance(check, dict) else None
    if not isinstance(dual, dict) or dual.get("policy") not in REMOTE_POLICIES:
        return []
    selected = dual.get("selected")
    selected_index = selected.get("index") if isinstance(selected, dict) else None
    boxes = []
    for position, candidate in enumerate(dual.get("candidates") or []):
        if not isinstance(candidate, dict):
            continue
        box = _valid_box(candidate.get("xyxy"))
        if box is None:
            continue
        api_index = candidate.get("index")
        if isinstance(api_index, bool) or not isinstance(api_index, int):
            api_index = position
        boxes.append(
            {
                "index": len(boxes),
                "api_index": api_index,
                "xyxy": box,
                "selected": api_index == selected_index,
                "cls": candidate.get("cls", ""),
            }
        )
    return boxes


def qingtong_seal_regions(source, check) -> list[SealRegion]:
    """Express the QingTong boxes in the page fractions the pipeline speaks."""
    boxes = qingtong_region_boxes(check)
    if not boxes:
        return []
    try:
        width, height = read_image_size(source)
    except (OSError, ValueError):
        return []
    if width <= 0 or height <= 0:
        return []
    regions = []
    for item in boxes:
        x1 = max(0.0, min(1.0, item["xyxy"][0] / width))
        y1 = max(0.0, min(1.0, item["xyxy"][1] / height))
        x2 = max(0.0, min(1.0, item["xyxy"][2] / width))
        y2 = max(0.0, min(1.0, item["xyxy"][3] / height))
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            continue
        regions.append(
            SealRegion(
                x=x1,
                y=y1,
                width=x2 - x1,
                height=y2 - y1,
                color=REMOTE_COLOR,
                role=classify_seal_role((x1 + x2) / 2, (y1 + y2) / 2),
                pixel_ratio=0.0,
                qingtong_cls=item["cls"],
            )
        )
    return regions


def qingtong_region_artifacts(
    source,
    check,
    artifact_dir,
    artifact_url_prefix: str = "",
    *,
    ocr_backend_label: str = "",
    note: str = "",
) -> list[dict]:
    """Plain crops of the QingTong boxes, for when the local OCR pass is off.

    The boxes are written straight from their pixel coordinates, so the evidence
    images stay identical to the region list even though no derivative crops
    were produced.
    """
    boxes = qingtong_region_boxes(check)
    if not artifact_dir or not boxes:
        return []
    prefix = str(artifact_url_prefix or "").rstrip("/")
    regions = qingtong_seal_regions(source, check)
    artifacts = []
    for position, item in enumerate(boxes):
        region = regions[position] if position < len(regions) else None
        destination = Path(artifact_dir) / "seals" / f"qingtong-{item['index']}.png"
        try:
            save_pixel_region_crop(source, destination, item["xyxy"])
        except (OSError, ValueError):
            continue
        artifacts.append(
            {
                "index": item["index"],
                "api_index": item["api_index"],
                "color": region.color if region else REMOTE_COLOR,
                "role": region.role if region else "",
                "shape": REMOTE_SHAPE,
                "source": REMOTE_SOURCE,
                "ocr_backend": ocr_backend_label,
                "note": note,
                "original_url": f"{prefix}/seals/{destination.name}" if prefix else "",
                "isolated_url": "",
                "unwrapped_url": "",
            }
        )
    return artifacts
