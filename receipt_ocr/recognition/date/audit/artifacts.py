"""Publish one date-region's evidence without running recognition audits."""

from __future__ import annotations

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.date.contracts import DateCropRun, DateCropRegion


def _scaled(row: TextObservation, box: tuple[float, float, float, float]):
    x, y, width, height = box
    return type(row)(
        text=row.text,
        confidence=row.confidence,
        x=x + row.x * width,
        y=y + row.y * height,
        width=row.width * width,
        height=row.height * height,
    )


def _url(prefix: str, path) -> str:
    if path is None:
        return ""
    return f"{prefix.rstrip('/')}/date/{path.name}"


def _publish_date_region(run: DateCropRun, region: DateCropRegion) -> None:
    """Project typed reads into the existing review payload.

    The original crop is exposed only as a thumbnail. Every row in
    ``decision_rows`` comes from a clean derivative and is therefore safe for
    the date stage to consume.
    """
    image = region.images
    line_box = image.line_box or (0.0, 0.0, 1.0, 1.0)
    region_box = (image.x, image.y, image.width, image.height)
    # OCR boxes are first expressed in the date-line crop, then in the single
    # date-region crop.  Publish the same boxes in page-normalized coordinates
    # so the decision result and the full-page preview use one coordinate space.
    line_rows_in_region = [
        _scaled(row, line_box) for row in region.evidence.line_rows
    ]
    line_rows = [_scaled(row, region_box) for row in line_rows_in_region]
    region_rows = [_scaled(row, region_box) for row in region.evidence.variant_rows]
    run.primary_rows = line_rows
    run.output.extend(region_rows)
    run.output.extend(line_rows)
    run.date_crop_entries.append(
        {
            "crop_key": region.crop_key,
            "tight": region.tight,
            "audit_only": False,
            "line_rows": list(region.evidence.line_rows),
            "line_raw": image.line_raw,
            "line_recognition": image.line_color_clean,
            "line_box": image.line_box,
            "region_box": region_box,
            "line_variants": region.evidence.line_variants,
        }
    )
    if not run.artifact_dir:
        return
    prefix = run.artifact_url_prefix.rstrip("/")
    run.artifacts.append(
        {
            "variant": "日期区域",
            "ocr_backend": run.services.backend_label(run.ocr_backend),
            "original_url": _url(prefix, image.raw),
            "color_clean_url": _url(prefix, image.color_clean),
            "line_clean_url": _url(prefix, image.crop),
            "date_line_original_url": _url(prefix, image.line_raw),
            "date_line_color_clean_url": _url(prefix, image.line_color_clean),
            "date_line_table_clean_url": _url(prefix, image.line_table_clean),
            "date_line_positioned_frame_clean_url": _url(
                prefix, image.line_positioned_frame_clean
            ),
            "ocr_texts": [row.text for row in region.evidence.variant_rows],
            "decision_rows": [row.to_dict() for row in line_rows],
            "date_inputs": [
                {
                    "id": item.id,
                    "kind": item.kind.value,
                    "role": item.role.value,
                    "label": item.label,
                    "coordinate_space": item.coordinate_space,
                }
                for item in (region.prepared.inputs if region.prepared else ())
            ],
            "ocr_variants": region.evidence.ocr_variants,
            "date_line_ocr_backend": (
                run.services.backend_label(region.evidence.line_backend)
                if region.evidence.line_backend
                else ""
            ),
            "date_line_ocr_variants": region.evidence.line_variants,
            "date_line_display_ocr_variants": region.evidence.display_line_variants,
            "date_line_component_candidate": region.evidence.component_candidate,
            "date_line_component_confidence": region.evidence.component_candidate_confidence,
        }
    )
