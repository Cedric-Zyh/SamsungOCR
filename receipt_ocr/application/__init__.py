"""Application services and recognition orchestration."""

from .pipeline import STAGES, complete_result, execute_stage, run_legacy
from .plans import validate_config, run_configured

__all__ = [
    "STAGES",
    "RecognitionService",
    "resolve_recognition_options",
    "complete_result",
    "execute_stage",
    "run_legacy", "validate_config", "run_configured",
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
