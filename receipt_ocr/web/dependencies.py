"""Explicit dependencies for each HTTP feature, supplied by the composition root."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from logging import Logger
from ..persistence.database import Database
from ..jobs.store import JobStore
from ..jobs.worker import JobWorker
from ..recognition.seal.reference.matcher import SealReferenceMatcher
from ..application.document_service import DocumentRecognitionService

@dataclass(frozen=True)
class QueueDependencies:
    allowed_extensions: set[str]
    data_dir: Path
    upload_dir: Path
    seal_api_enabled: bool
    database: Database
    job_store: JobStore
    job_worker: JobWorker | None


@dataclass(frozen=True)
class RecognitionDependencies:
    allowed_extensions: set[str]
    data_dir: Path
    upload_dir: Path
    documents: DocumentRecognitionService
    seal_api_enabled: bool
    logger: Logger
    database: Database
    now_iso: Callable


@dataclass(frozen=True)
class ResultDependencies:
    ground_truth_path: Path
    result_filters: tuple[str, ...]
    project_result: Callable
    database: Database
    job_store: JobStore
    load_ground_truth: Callable


@dataclass(frozen=True)
class ReviewDependencies:
    ground_truth_path: Path
    apply_edits: Callable
    confirmation_error: Callable
    project_result: Callable
    review_revision: Callable
    with_review_revision: Callable
    database: Database
    seal_reference_matcher: SealReferenceMatcher

