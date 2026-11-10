"""Explicit input contracts passed to recognition stages.

The request object is deliberately independent from Flask, the database, and
any OCR provider.  A stage receives everything it needs through this object,
which keeps orchestration code testable and makes future provider adapters
replaceable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from ..domain.ocr import TextObservation


@dataclass(frozen=True)
class QingTongEvidence:
    """Settled provider evidence: stages never own a request or a Future."""

    response: dict | None = None
    error: str | None = None

    def require_response(self) -> dict:
        if self.error is not None:
            raise RuntimeError(self.error)
        if self.response is None:
            raise RuntimeError("清瞳印章请求未返回结果")
        return deepcopy(self.response)


@dataclass(frozen=True)
class StageRequest:
    """Configuration and per-run state supplied to one recognition stage."""

    route: Mapping[str, str]
    fields: Mapping[str, str] = field(default_factory=dict)
    artifact_dir: Path | str | None = None
    artifact_url_prefix: str = ""
    seal_mode: str = "local"
    seal_evidence: QingTongEvidence | None = None
    acceptance: Mapping[str, Any] = field(default_factory=dict)
    seal_orientation_mode: str = "polygon"
    seal_page_rows: tuple[TextObservation, ...] | None = None
