"""Policy for an optional additional OCR read.

The normal seal pipeline always remains ``preprocess -> OCR -> postprocess``.
This module only decides whether a low-confidence or conflicting result should
be read again with another configured provider.  Provider and model names stay
at this boundary; the seal workflow never contains a provider-specific branch.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from receipt_ocr.providers.paddle_runtime import is_paddle_backend, variant_of

SECONDARY_READ_MODES = ("auto", "local", "off")
DEFAULT_SECONDARY_READ_MODE = "auto"

# The provider the secondary read was written against, and the lightweight tier it reads
# the robust bounds bands with.  Two different tiers keep the cross-model common
# suffix an actual cross-model observation.
DEFAULT_SECONDARY_READER = "paddle_v6"
SECONDARY_BAND_READER = None

# Used by ``local`` when the seal stage is served by a non-Paddle provider
# (单证通 / 清瞳).  The colour-isolated derivatives are only wired to the local
# Paddle pipelines.
SECONDARY_FALLBACK_READER = "paddle_v6"

SKIP_DISABLED = "按设置跳过印章补充识别（SEAL_SECONDARY_READ_MODE=off）"
SKIP_UNAUTHORISED = (
    "印章补充识别未获授权：当前识别方案未允许 "
    f"{DEFAULT_SECONDARY_READER}，已跳过并保留常规证据"
)
SKIP_SAME_MODEL = (
    "印章补充识别器与常规识别相同（{backend}），"
    "重复读取不构成独立佐证，已跳过"
)


@dataclass(frozen=True)
class SecondaryReadPolicy:
    """Resolved additional-read decision for one receipt."""

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
        """Secondary read readings come from a model the regular seal pass did not use."""
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


def secondary_read_mode() -> str:
    """Configured ``SEAL_SECONDARY_READ_MODE``, falling back to ``auto`` when unknown."""
    configured = (
        os.getenv("SEAL_SECONDARY_READ_MODE")
        or ""
    ).strip().lower()
    if configured in SECONDARY_READ_MODES:
        return configured
    return DEFAULT_SECONDARY_READ_MODE


def variant_for_reader(backend: str | None) -> str:
    """Model variant behind a secondary read backend id, defaulting to v6.

    The secondary reader is always a local Paddle tier by construction, so the
    line recogniser can be addressed by variant directly.
    """
    return variant_of(backend) or "v6"


def resolve_secondary_read(
    seal_backend: str | None,
    *,
    allowed: frozenset | set | None = None,
) -> SecondaryReadPolicy:
    """Decide the secondary reader for one receipt.

    ``allowed`` is the request's provider allowlist, or ``None`` while the run is
    unrestricted (``analyzer.analyze`` and the offline tools).
    """
    mode = secondary_read_mode()
    selected = str(seal_backend or "").strip().lower()

    if mode == "off":
        return SecondaryReadPolicy(
            mode=mode, seal_backend=selected, skip_reason=SKIP_DISABLED
        )

    if mode == "local":
        backend = (
            selected if is_paddle_backend(selected) else SECONDARY_FALLBACK_READER
        )
        if backend == selected:
            return SecondaryReadPolicy(
                mode=mode,
                seal_backend=selected,
                skip_reason=SKIP_SAME_MODEL.format(backend=backend),
            )
        # No band reader: loading a second Paddle tier is exactly the cost this
        # mode exists to avoid, so the cross-model suffix route stays off.
        return SecondaryReadPolicy(
            mode=mode,
            seal_backend=selected,
            backend=backend,
            band_backend=None,
            authorise=frozenset({backend}),
        )

    # ``auto`` uses the remaining local model only for remote seal stages.
    if mode == "auto" and allowed is not None and DEFAULT_SECONDARY_READER not in allowed:
        return SecondaryReadPolicy(
            mode=mode, seal_backend=selected, skip_reason=SKIP_UNAUTHORISED
        )
    if selected == DEFAULT_SECONDARY_READER:
        return SecondaryReadPolicy(
            mode=mode,
            seal_backend=selected,
            skip_reason=SKIP_SAME_MODEL.format(backend=DEFAULT_SECONDARY_READER),
        )
    return SecondaryReadPolicy(
        mode=mode,
        seal_backend=selected,
        backend=DEFAULT_SECONDARY_READER,
        band_backend=SECONDARY_BAND_READER,
        authorise=frozenset({DEFAULT_SECONDARY_READER}),
    )


def secondary_read_providers_for_plan(config: dict | None) -> frozenset:
    """Extra providers the seal stage must authorize for an additional read.

    ``recognition_config`` scopes every stage to the single backend the user
    picked.  Only the explicit ``local`` mode widens that scope; ``auto`` never
    does, so turning the secondary read on stays a deliberate choice.
    """
    mode = secondary_read_mode()
    if mode != "local":
        return frozenset()
    extra: set[str] = set()
    # Resolve every configured seal provider. Remote seal providers use the
    # local v6 fallback in ``local`` mode; a v6 primary is correctly skipped
    # because it would repeat the same reader.
    for method in (config or {}).get("seal", []):
        backend = str(method or "").strip().lower()
        extra |= resolve_secondary_read(backend, allowed=None).authorise
    return frozenset(extra)


def describe_secondary_read(mode: str | None = None) -> dict:
    """Status payload for the settings surface and the diagnostic logs."""
    active = mode or secondary_read_mode()
    return {
        "mode": active,
        "modes": list(SECONDARY_READ_MODES),
        "backend": DEFAULT_SECONDARY_READER,
        "band_backend": SECONDARY_BAND_READER,
        "independent": active == "auto",
        "description": {
            "auto": "仅在印章阶段使用远程方式时用本地 v6 复算，否则跳过并记录原因",
            "local": "使用本地 v6 复算，不加载已下线模型",
            "off": "完全跳过印章二次审计",
        }[active],
    }


variant_for_secondary_backend = variant_for_reader
