"""Keep the existing result keys stable while stages use domain objects."""

from copy import deepcopy

from ..domain.results import (
    DateStageResult, FieldStageResult, HandwritingStageResult,
    ProductStageResult, SealStageResult, StageResult,
)


def stage_result_payload(result: StageResult) -> dict:
    """Return an independent payload, preserving optional and empty fields."""
    output = {"stage_review_reasons": result.review_reasons}
    if isinstance(result, FieldStageResult):
        output.update(fields=result.fields, field_metadata=result.metadata,
                      field_fallbacks=result.fallbacks, qr_text=result.qr_text)
        if result.requirement_artifacts:
            output["processing_artifacts"] = {"signature_requirement": result.requirement_artifacts}
    elif isinstance(result, ProductStageResult):
        output["product_table"] = result.table
    elif isinstance(result, HandwritingStageResult):
        output.update(handwriting_fields=result.fields, handwriting_metadata=result.metadata)
    elif isinstance(result, DateStageResult):
        output.update(date_check=result.check, date_ocr_texts=result.ocr_texts,
                      processing_artifacts={"date": result.artifacts},
                      safety_policy=result.safety_policy, preview_date_box=result.preview_box)
    elif isinstance(result, SealStageResult):
        output.update(seal_check=result.check, seal_regions=result.regions,
                      processing_artifacts={"seals": result.artifacts},
                      safety_policy=result.safety_policy)
    else:
        raise TypeError(f"不支持的阶段结果：{type(result).__name__}")
    return deepcopy(output)
