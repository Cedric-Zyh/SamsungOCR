"""Policy for the bounded second-model seal audit.

The audit re-reads colour-isolated seal derivatives so a faint, clipped or
overlapping stamp can still be recovered.  It is the only part of the local
pipeline that may load a model the recognition plan did not ask for, so this
module is the single place that decides *whether* it runs and *which* provider
it uses.

``SEAL_AUDIT_MODE`` selects the strategy:

``auto``    Default, and deliberately behaviour-preserving.  Audit with
            PP-OCRv5 Server when the current request already authorises it,
            otherwise skip and record why.  An unrestricted ``analyzer.analyze``
            run still audits; a scoped recognition plan that never allowed the
            Server model still does not.  What changes is that the skip is now
            explicit and attributable instead of a silent empty result.
``server``  Always audit with PP-OCRv5 Server and widen the seal stage's
            authorisation so that provider may load.  The full cross-model
            check, opt-in.
``local``   Audit with a local Paddle tier instead of Server, and never load
            Server.  Roughly a quarter of the cost.  Independent whenever the
            seal stage runs on a different model tier.
``off``     Never audit.

Background: the audit used to call ``recognize_text(..., backend=
"paddle_server")`` directly.  Under a recognition plan the provider scope is
exactly the backend the user selected, so every audit call was denied and
returned ``[]`` -- the branch produced nothing while still looking as though it
had run.  The same image therefore carried different seal evidence depending on
which entry point processed it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .paddle_ocr import is_paddle_backend, variant_of

SEAL_AUDIT_MODES = ("auto", "server", "local", "off")
DEFAULT_SEAL_AUDIT_MODE = "auto"

# The provider the audit was written against, and the lightweight tier it reads
# the robust bounds bands with.  Two different tiers keep the cross-model common
# suffix an actual cross-model observation.
DEFAULT_AUDIT_BACKEND = "paddle_server"
BAND_READER_BACKEND = "paddle"

# Used by ``local`` when the seal stage is served by a non-Paddle provider
# (单证通 / 清瞳).  The colour-isolated derivatives are only wired to the local
# Paddle pipelines.
LOCAL_FALLBACK_BACKEND = "paddle"

SKIP_DISABLED = "按设置跳过印章二次审计（SEAL_AUDIT_MODE=off）"
SKIP_UNAUTHORISED = (
    "印章二次审计未获授权：当前识别方案未允许 "
    f"{DEFAULT_AUDIT_BACKEND}，已跳过并保留常规证据"
)
SKIP_SAME_MODEL = (
    "印章二次审计后端与常规识别同为 {backend}，"
    "重复读取不构成独立佐证，已跳过"
)


@dataclass(frozen=True)
class SealAuditPlan:
    """Resolved audit decision for one receipt."""

    mode: str
    seal_backend: str = ""
    backend: str | None = None
    band_backend: str | None = None
    skip_reason: str = ""
    authorise: frozenset = field(default_factory=frozenset)

    @property
    def runs(self) -> bool:
        return self.backend is not None

    @property
    def independent(self) -> bool:
        """Audit readings come from a model the regular seal pass did not use."""
        return bool(self.runs and self.backend != self.seal_backend)

    @property
    def band_pair(self) -> bool:
        """The robust-bands branch has two genuinely different tiers to compare.

        ``_shared_long_organization_suffix`` only accepts a suffix both models
        read on their own.  Pointing it at two copies of one model would turn a
        self-consistency check into a false cross-model claim.
        """
        return bool(
            self.runs
            and self.band_backend
            and self.band_backend != self.backend
        )

    @property
    def note(self) -> str:
        """Short human-readable summary for the artifact and the logs."""
        if not self.runs:
            return self.skip_reason
        kind = "跨模型佐证" if self.independent else "同族复算，非独立佐证"
        return f"印章二次审计使用 {self.backend}（{kind}）"


def seal_audit_mode() -> str:
    """Configured ``SEAL_AUDIT_MODE``, falling back to ``auto`` when unknown."""
    configured = (os.getenv("SEAL_AUDIT_MODE") or "").strip().lower()
    if configured in SEAL_AUDIT_MODES:
        return configured
    return DEFAULT_SEAL_AUDIT_MODE


def variant_for_audit_backend(backend: str | None) -> str:
    """Model variant behind an audit backend id, defaulting to Mobile.

    The audit provider is always a local Paddle tier by construction, so the
    line recogniser can be addressed by variant directly.
    """
    return variant_of(backend) or "mobile"


def resolve_seal_audit(
    seal_backend: str | None,
    *,
    allowed: frozenset | set | None = None,
) -> SealAuditPlan:
    """Decide the audit provider for one receipt.

    ``allowed`` is the request's provider allowlist, or ``None`` while the run is
    unrestricted (``analyzer.analyze`` and the offline tools).
    """
    mode = seal_audit_mode()
    selected = str(seal_backend or "").strip().lower()

    if mode == "off":
        return SealAuditPlan(
            mode=mode, seal_backend=selected, skip_reason=SKIP_DISABLED
        )

    if mode == "local":
        backend = (
            selected if is_paddle_backend(selected) else LOCAL_FALLBACK_BACKEND
        )
        if backend == selected:
            return SealAuditPlan(
                mode=mode,
                seal_backend=selected,
                skip_reason=SKIP_SAME_MODEL.format(backend=backend),
            )
        # No band reader: loading a second Paddle tier is exactly the cost this
        # mode exists to avoid, so the cross-model suffix route stays off.
        return SealAuditPlan(
            mode=mode,
            seal_backend=selected,
            backend=backend,
            band_backend=None,
            authorise=frozenset({backend}),
        )

    # ``auto`` and ``server`` both want the Server model.
    if mode == "auto" and allowed is not None and DEFAULT_AUDIT_BACKEND not in allowed:
        return SealAuditPlan(
            mode=mode, seal_backend=selected, skip_reason=SKIP_UNAUTHORISED
        )
    if selected == DEFAULT_AUDIT_BACKEND:
        return SealAuditPlan(
            mode=mode,
            seal_backend=selected,
            skip_reason=SKIP_SAME_MODEL.format(backend=DEFAULT_AUDIT_BACKEND),
        )
    return SealAuditPlan(
        mode=mode,
        seal_backend=selected,
        backend=DEFAULT_AUDIT_BACKEND,
        band_backend=BAND_READER_BACKEND,
        authorise=frozenset({DEFAULT_AUDIT_BACKEND, BAND_READER_BACKEND}),
    )


def seal_audit_providers_for_plan(config: dict | None) -> frozenset:
    """Extra providers the seal stage must authorise so its audit can run.

    ``recognition_config`` scopes every stage to the single backend the user
    picked.  Only the explicit ``server`` / ``local`` modes widen that scope;
    ``auto`` never does, so turning the audit on stays a deliberate choice.
    """
    mode = seal_audit_mode()
    if mode not in {"server", "local"}:
        return frozenset()
    extra: set[str] = set()
    # Any of the local seal backends may run the audit, and a single stage call
    # only knows its own method, so authorise the union.
    for method in (config or {}).get("seal", []):
        backend = str(method or "").strip().lower()
        if not is_paddle_backend(backend):
            continue
        extra |= resolve_seal_audit(backend, allowed=None).authorise
    return frozenset(extra)


def describe_seal_audit(mode: str | None = None) -> dict:
    """Status payload for the settings surface and the diagnostic logs."""
    active = mode or seal_audit_mode()
    return {
        "mode": active,
        "modes": list(SEAL_AUDIT_MODES),
        "backend": DEFAULT_AUDIT_BACKEND,
        "band_backend": BAND_READER_BACKEND,
        "independent": active in {"auto", "server"},
        "description": {
            "auto": "已授权时用 Server 跨模型审计，否则跳过并记录原因",
            "server": "始终用 Server 跨模型审计，并为其放开授权",
            "local": "改用本地轻量模型复算，不加载 Server",
            "off": "完全跳过印章二次审计",
        }[active],
    }
