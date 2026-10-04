"""One recognition entry point shared by HTTP, jobs, and command-line tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any



@dataclass(frozen=True)
class RecognitionService:
    """Coordinate one receipt recognition request.

    ``analyzer`` remains injected rather than imported globally.  This keeps
    provider construction and test doubles outside the use-case boundary.
    ``reference_matcher`` is supplied by the application composition root and
    is passed to the same plan executor for every request.
    """

    analyzer: Any
    reference_matcher: Any = None

    def recognize(self, *args: Any, recognition_config=None,
                  previous_fields=None, **kwargs: Any) -> dict:
        kwargs["reference_matcher"] = self.reference_matcher
        return self.analyzer.analyze(
            *args,
            recognition_config=recognition_config,
            previous_fields=previous_fields,
            **kwargs,
        )
