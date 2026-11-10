"""Explicit inputs to the sample runtime; no Flask or application globals."""
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import Callable

from receipt_ocr.persistence.database import Database


@dataclass(frozen=True)
class SamplePaths:
    data_dir: Path
    ground_truth_path: Path
    allowed_extensions: frozenset[str]


@dataclass(frozen=True)
class SampleDependencies:
    database: Database
    paths: SamplePaths
    analyze: Callable
    project_result: Callable
    seal_api_enabled: bool
    logger: Logger
