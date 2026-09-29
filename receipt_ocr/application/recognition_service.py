"""Use-case boundary for configured receipt recognition.

Historically the Flask module owned the choice between the legacy analyzer
and the multi-provider configured runner.  Keeping that decision in a small
application service gives HTTP routes, background jobs, and command-line
tools one stable entry point while the analyzer is being reorganized.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .plans import run_configured


@dataclass(frozen=True)
class RecognitionService:
    """Coordinate one receipt recognition request.

    ``analyzer`` remains injected rather than imported globally.  This keeps
    provider construction and test doubles outside the use-case boundary.
    ``reference_matcher`` is supplied by the application composition root and
    is passed to both recognition paths consistently.
    """

    analyzer: Any
    reference_matcher: Any = None

    def recognize(self, *args: Any, recognition_config=None,
                  previous_fields=None, **kwargs: Any) -> dict:
        kwargs["reference_matcher"] = self.reference_matcher
        if recognition_config is None:
            return self.analyzer.analyze(*args, **kwargs)
        return run_configured(
            self.analyzer,
            *args,
            config=recognition_config,
            previous_fields=previous_fields,
            **kwargs,
        )

