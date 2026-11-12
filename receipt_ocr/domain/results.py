"""Stage-owned results, before conversion to the persisted JSON contract.

Each stage can publish only its own fields. Provider-specific check metadata
and artifact records remain lossless dictionaries during their migration;
they are not discarded or reduced to a boolean verdict.
"""

from dataclasses import dataclass, field
from collections.abc import Mapping, Iterator
from copy import deepcopy
from typing import Any, Literal
from .issues import CheckResult
from .evidence_selection import inactive

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


@dataclass(frozen=True)
class StageOutcome(Mapping[str, Any]):
    """One provider's settled stage outcome before the JSON boundary.

    Provider payloads remain lossless. The read-only mapping view lets existing
    comparison helpers migrate independently; scheduling and assembly use the
    explicit state instead of interpreting the presence of arbitrary keys.
    """

    stage: str
    method: str
    state: Literal["completed", "failed", "pending", "skipped"]
    payload: dict | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.state not in {"completed", "failed", "pending", "skipped"}:
            raise ValueError(f"Invalid stage outcome state: {self.state}")
        if self.state in {"completed", "skipped"} and self.payload is None:
            raise ValueError(f"{self.state} stage outcome requires result evidence")

    @property
    def has_result(self) -> bool:
        return self.payload is not None

    @classmethod
    def from_variant(cls, stage: str, variant) -> "StageOutcome":
        """Decode internal ``result`` or the historical public ``details`` form.

        Display values are not observations. Unknown envelope metadata remains
        in the internal snapshot, while public serialization has its own narrow
        field contract. A nonempty public payload has the same precedence used
        by the saved-evidence readers.
        """
        if isinstance(variant, cls):
            if variant.stage != stage:
                raise ValueError(f"Stage outcome belongs to {variant.stage}, not {stage}")
            return variant
        payload = variant.get("result")
        details = variant.get("details")
        if isinstance(details, Mapping) and (details or not isinstance(payload, Mapping)):
            payload = details
        failed = variant.get("error") or any(
            str(variant.get(key, "")).lower() in {"failed", "error", "识别失败"}
            for key in ("status", "execution_status"))
        state = ("pending" if variant.get("pending") else
                 "failed" if failed else
                 "skipped" if isinstance(payload, dict) and
                 (payload.get(f"{stage}_check") or {}).get("execution_status") == "skipped" else
                 "completed" if payload is not None else "failed")
        return cls(stage, variant["method"], state, payload=payload, error=variant.get("error"),
                   metadata={key: value for key, value in variant.items()
                             if key not in {"method", "details", "value"}
                             and (key not in {"result", "error"} or value is None)})

    @classmethod
    def completed(cls, stage: str, method: str, payload: dict) -> "StageOutcome":
        return cls.from_variant(stage, {"method": method, "result": payload})

    @classmethod
    def failed(cls, stage: str, method: str, error: str) -> "StageOutcome":
        return cls(stage, method, "failed", error=error)

    def to_variant(self, *, copy_payload=True) -> dict:
        value = {**self.metadata, "method": self.method}
        if self.state == "pending":
            value["pending"] = True
        if self.has_result:
            value["result"] = self.payload
            if self.state == "skipped":
                key = f"{self.stage}_check"
                value["result"] = {**self.payload, key: {
                    **(self.payload.get(key) or {}), "execution_status": "skipped"}}
        if self.error is not None:
            value["error"] = self.error
        if self.state == "failed" and not inactive(value):
            # A constructed failure can retain an old payload without an error
            # message. Its execution state must still survive the JSON boundary.
            value["execution_status"] = "failed"
        return deepcopy(value) if copy_payload else value

    def __getitem__(self, key):
        return self.to_variant(copy_payload=False)[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_variant(copy_payload=False))

    def __len__(self) -> int:
        return len(self.to_variant(copy_payload=False))
