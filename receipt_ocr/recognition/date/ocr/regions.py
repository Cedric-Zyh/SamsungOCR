"""Read only the declared clean inputs for the compact date region."""
from ..contracts import DateCropRun, DateCropRegion
from ..preprocess.manifest import ensure_manifest
from ..postprocess.reads import structure_result, date_rows
from .interface import DateOcrReader


def _record_result(region, result):
    result = structure_result(result)
    region.evidence.ocr_results.append(result)
    region.evidence.reads.extend(result.reads)
    return date_rows(result)


def _recognize_region_variants(run: DateCropRun, region: DateCropRegion) -> None:
    evidence = region.evidence
    evidence.variant_rows = []
    raw = {"preprocessing": "原始裁剪", "ocr_texts": [], "accepted_texts": [],
           "recognition_scope": "仅展示，不参与识别"}
    evidence.ocr_variants = [dict(raw)]
    evidence.secondary_ocr_variants = []
    reader = DateOcrReader()
    for image in ensure_manifest(region).for_ocr(coordinate_space="region"):
        result = reader.read(image, provider=run.ocr_backend, line=False,
                             recognize_text=run.services.recognize_text,
                             min_text_height=0.02,
                             custom_words=[])
        rows = _record_result(region, result)
        run.inputs.append(image)
        run.reads.extend(result.reads)
        evidence.variant_rows.extend(rows)
        evidence.ocr_variants.append({"preprocessing": image.label,
                                      "ocr_texts": [row.text for row in rows]})


def _recognize_region_lines(run: DateCropRun, region: DateCropRegion) -> None:
    from receipt_ocr.providers.paddle_runtime import is_lightweight_backend
    evidence = region.evidence
    evidence.line_backend = run.ocr_backend if is_lightweight_backend(run.ocr_backend) else ""
    evidence.line_rows = []
    evidence.accepted_line_rows = []
    evidence.display_line_rows = []
    evidence.line_variants = []
    evidence.display_line_variants = []
    evidence.cross_model_month_day_confirmed = False
    if not evidence.line_backend:
        return
    evidence.line_variants.append({"preprocessing": "日期行原图", "ocr_texts": [],
        "accepted_texts": [], "recognition_scope": "仅展示，不参与识别"})
    reader = DateOcrReader()
    for image in ensure_manifest(region).for_ocr(coordinate_space="line"):
        result = reader.read(image, provider=evidence.line_backend)
        rows = _record_result(region, result)
        run.inputs.append(image)
        run.reads.extend(result.reads)
        evidence.line_rows.extend(rows)
        evidence.accepted_line_rows.extend(rows)
        evidence.line_variants.append({"preprocessing": image.label,
            "ocr_texts": [row.text for row in rows],
            "accepted_texts": [row.text for row in rows]})
        if image.kind.value == "frame_clean":
            evidence.display_line_rows.extend(rows)
            evidence.display_line_variants.append({"preprocessing": image.label,
                "ocr_texts": [row.text for row in rows]})
