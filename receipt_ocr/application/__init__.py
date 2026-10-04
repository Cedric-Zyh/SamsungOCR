"""Application services and recognition orchestration."""

from .pipeline import (
    STAGES,
    complete_result,
    execute_stage,
    normalize_result_contract,
)
from .plans import validate_config, run_configured
from .stage_coordinator import ReceiptAnalyzer

__all__ = [
    "STAGES",
    "RecognitionService",
    "ReceiptAnalyzer",
    "resolve_recognition_options",
    "complete_result",
    "execute_stage",
    "validate_config", "run_configured", "normalize_result_contract",
]


def __getattr__(name):
    """Load the configured recognition service after the pipeline facade.

    The concrete services import provider and persistence details lazily so the
    package itself remains a lightweight composition boundary.
    The package-level export remains available to callers while the concrete
    service is loaded lazily.
    """

    if name == "RecognitionService":
        from .recognition_service import RecognitionService

        return RecognitionService
    if name == "resolve_recognition_options":
        from .options import resolve_recognition_options

        return resolve_recognition_options
    raise AttributeError(name)
