"""Explicit input contracts passed to recognition stages.

The request object is deliberately independent from Flask, the database, and
any OCR provider.  A stage receives everything it needs through this object,
which keeps orchestration code testable and makes future provider adapters
replaceable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class StageRequest:
    """Configuration and per-run state supplied to one recognition stage."""

    route: Mapping[str, str]
    fields: Mapping[str, str] = field(default_factory=dict)
    artifact_dir: Path | str | None = None
    artifact_url_prefix: str = ""
    seal_mode: str = "local"
    seal_future: Any = None
    acceptance: Mapping[str, Any] = field(default_factory=dict)
    seal_orientation_mode: str = "polygon"
