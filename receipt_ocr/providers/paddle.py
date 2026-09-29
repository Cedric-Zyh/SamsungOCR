"""Lazy adapter for the local Paddle runtime.

The runtime owns model loading, locks and inference; this adapter owns only
provider-to-model dispatch. It does not import Paddle until recognition runs.
"""

from dataclasses import dataclass
from pathlib import Path

from ..domain.ocr import TextObservation
from .registry import PROVIDERS


@dataclass(frozen=True)
class PaddleTextProvider:
    provider_id: str

    def recognize(self, image_path: str | Path, **options) -> list[TextObservation]:
        definition = PROVIDERS.require(self.provider_id)
        if definition.kind != "local_text":
            raise ValueError(f"识别方式不支持通用文字识别：{self.provider_id}")
        from ..runtime.scope import provider_allowed
        if not provider_allowed(definition.id):
            return []
        from .paddle_runtime import recognize_text
        options["model_variant"] = definition.model_variant
        return recognize_text(image_path, **options)
