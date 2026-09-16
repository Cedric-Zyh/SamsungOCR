"""Collect ordered local seal evidence without deciding the document verdict."""

from __future__ import annotations

import tempfile
from contextlib import nullcontext
from pathlib import Path

from .execution import timed
from .image_processing import SealRegion
from .seal_crop_regular import (
    _collect_primary_region_evidence,
    _collect_secondary_region_evidence,
    _record_region_evidence,
)
from .seal_crop_mobile import _resolve_mobile_company_conflict
from .seal_crop_routes import _plan_server_audit_route, _rank_server_audit_candidates
from .seal_crop_audit_images import _prepare_server_audit_images
from .seal_crop_audit import (
    _read_server_audit_evidence,
    _reconstruct_server_audit_evidence,
    _record_server_audit_artifact,
    _combine_overlapping_server_evidence,
)
from .seal_crop_types import (
    SealCropRequest,
    SealEvidenceCollection,
    SealRegionEvidence,
    SealAuditRoute,
    SealAuditEvidence,
)


@timed("local_seal_recognition")
def _recognize_local_seals(
    source: Path,
    rows: list,
    regions: list[SealRegion],
    artifact_dir: str | Path | None,
    artifact_url_prefix: str,
    ocr_backend: str,
    secondary_ocr_backend: str | None = None,
    requirement: str = "",
    footer_anchor_y: float | None = None,
) -> tuple[list[str], list[dict]]:
    request = SealCropRequest(
        source=source,
        rows=rows,
        artifact_dir=artifact_dir,
        artifact_url_prefix=artifact_url_prefix,
        ocr_backend=ocr_backend,
        secondary_ocr_backend=secondary_ocr_backend,
        requirement=requirement,
        footer_anchor_y=footer_anchor_y,
    )
    collection = SealEvidenceCollection()
    context = (
        nullcontext(str(Path(artifact_dir) / "seals"))
        if artifact_dir
        else tempfile.TemporaryDirectory(prefix="receipt-seals-")
    )
    with context as temp_dir:
        Path(temp_dir).mkdir(parents=True, exist_ok=True)
        # Crop lifetimes cover every primary, secondary and Server call.
        # Keep region order, model order and reconstruction order explicit.
        for index, region in enumerate(regions):
            evidence = SealRegionEvidence()
            _collect_primary_region_evidence(request, evidence, index, region, temp_dir)
            _collect_secondary_region_evidence(request, evidence, region)
            _record_region_evidence(request, collection, evidence, index, region)

        route = SealAuditRoute()
        _resolve_mobile_company_conflict(request, collection, route)
        _plan_server_audit_route(request, collection, route)
        if route.should_server_audit:
            _rank_server_audit_candidates(request, collection, route)
            for audit_position, candidate in enumerate(
                route.ranked_candidates[:route.audit_limit]
            ):
                audit = SealAuditEvidence()
                _prepare_server_audit_images(
                    request, route, audit, candidate, audit_position
                )
                _read_server_audit_evidence(audit)
                _reconstruct_server_audit_evidence(
                    request, collection, route, audit, candidate
                )
                _record_server_audit_artifact(
                    request, collection, route, audit, candidate
                )
            _combine_overlapping_server_evidence(request, collection, route)
    return collection.texts, collection.artifacts
