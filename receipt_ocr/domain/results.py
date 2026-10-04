"""Stage-owned results, before conversion to the persisted JSON contract.

Each stage can publish only its own fields. Provider-specific check metadata
and artifact records remain lossless dictionaries during their migration;
they are not discarded or reduced to a boolean verdict.
"""

from dataclasses import dataclass, field
from typing import Any
from .issues import CheckResult

Metadata = dict[str, dict[str, Any]]
Artifact = dict[str, Any]
Check = CheckResult


@dataclass(kw_only=True)
class FieldStageResult:
    fields: dict[str, str]
    metadata: Metadata
    fallbacks: Metadata
    qr_text: str
    requirement_artifacts: list[Artifact] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class ProductStageResult:
    table: dict[str, Any]
    review_reasons: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class HandwritingStageResult:
    fields: dict[str, str]
    metadata: Metadata
    review_reasons: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class DateStageResult:
    check: Check
    ocr_texts: list[str]
    artifacts: list[Artifact]
    preview_box: tuple[float, float, float, float] | None = None
    safety_policy: str = ""
    review_reasons: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class SealStageResult:
    check: Check
    regions: list[dict[str, Any]]
    artifacts: list[Artifact]
    safety_policy: str = ""
    review_reasons: list[str] = field(default_factory=list)


StageResult = FieldStageResult | ProductStageResult | HandwritingStageResult | DateStageResult | SealStageResult
