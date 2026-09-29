"""Domain contracts shared by recognition stages."""

from .ocr import TextObservation
from .recognition import RecognitionOptions
from .requests import StageRequest
from .results import (
    DateStageResult,
    FieldStageResult,
    HandwritingStageResult,
    ProductStageResult,
    SealStageResult,
)
from .stages import STAGES, Stage

__all__ = [
    "DateStageResult",
    "FieldStageResult",
    "HandwritingStageResult",
    "ProductStageResult",
    "RecognitionOptions",
    "STAGES",
    "SealStageResult",
    "Stage",
    "StageRequest",
    "TextObservation",
]
