"""Lossless JSON snapshots with an explicit, read-only restoration boundary."""

from copy import deepcopy
from dataclasses import dataclass, field

from ..domain.ocr import TextObservation
from ..domain.results import StageOutcome
from ..domain.stages import STAGES
from .context import DocumentContext
from .document_evidence import stage_outcomes
from .saved_footer import saved_footer_evidence


@dataclass(frozen=True)
class DocumentRoutingSnapshot:
    document_type: dict | None = None
    has_footer: bool | None = None
    footer_detection: dict = field(default_factory=dict)
    primary_backend: str | None = None
    extra: dict = field(default_factory=dict)
    present_fields: frozenset[str] = frozenset()

    @classmethod
    def from_json(cls, value):
        value = deepcopy(value or {})
        known = {key: value.pop(key) for key in
                 ("document_type", "has_footer", "footer_detection", "primary_backend") if key in value}
        return cls(**known, extra=value, present_fields=frozenset(known))

    def to_json(self):
        known = {"document_type": self.document_type, "has_footer": self.has_footer,
                 "footer_detection": self.footer_detection, "primary_backend": self.primary_backend}
        return deepcopy({**self.extra, **{key: known[key] for key in self.present_fields}})


@dataclass(frozen=True)
class DocumentSnapshot:
    config: dict
    results: dict[str, list[StageOutcome]]
    errors: list
    previous_fields: dict
    routing: DocumentRoutingSnapshot
    evidence: dict
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, value):
        saved = deepcopy(value)
        config = saved.pop("config")
        for stage in STAGES:
            config.setdefault(stage, [])
        return cls(
            config=config, results=stage_outcomes(saved.pop("results", {})),
            errors=saved.pop("errors", []), previous_fields=saved.pop("previous_fields", {}),
            routing=DocumentRoutingSnapshot.from_json(saved.pop("context", {})),
            evidence=saved.pop("evidence", {}), extra=saved,
        )

    @classmethod
    def capture(cls, context, config, results, errors, previous_fields):
        return cls.from_json({
            "config": config, "results": results, "errors": errors,
            "previous_fields": previous_fields,
            "context": {key: getattr(context, key) for key in
                        ("document_type", "has_footer", "footer_detection", "primary_backend")},
            "evidence": context.evidence(),
        })

    def to_json(self):
        return deepcopy({**self.extra, "config": self.config,
                         "results": {stage: [value.to_variant(copy_payload=False) for value in values]
                                     for stage, values in self.results.items()},
                         "errors": self.errors, "previous_fields": self.previous_fields,
                         "context": self.routing.to_json(), "evidence": self.evidence})

    def restore_context(self, source, filename):
        """Restore only saved routing and observations; never collaborators.

        Older snapshots may lack footer metadata. Reuse their saved spatial
        observations through the existing migration, without reading the image.
        Unknown context keys survive JSON round trips but cannot replace page(),
        source, caches, or an OCR provider on a live DocumentContext.
        """
        context = DocumentContext(source, filename=filename)
        routing = self.routing
        if routing.document_type:
            context.document_type = deepcopy(routing.document_type)
        if isinstance(routing.has_footer, bool):
            context.has_footer = routing.has_footer
        context.footer_detection = deepcopy(routing.footer_detection or {})
        context.primary_backend = routing.primary_backend
        footer = saved_footer_evidence(routing.to_json(), self.evidence)
        if isinstance(footer.get("present"), bool):
            context.has_footer = footer["present"]
            context.footer_detection = footer
        anchor = context.footer_detection.get("anchor")
        if isinstance(anchor, dict):
            context.footer_anchor = TextObservation(**anchor)
        context.primary_rows = [TextObservation(**row) for row in self.evidence.get("ocr_observations", [])]
        context._qr = self.evidence.get("qr_text", "")
        return context
