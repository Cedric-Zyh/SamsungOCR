"""Collect ordered local seal evidence without deciding the document verdict."""

from __future__ import annotations

import tempfile
from contextlib import nullcontext
from pathlib import Path

from .execution import timed
from .image_processing import SealRegion
from .recognition_scope import allowed_providers
from .seal_audit_policy import resolve_seal_audit
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
    from shutil import copyfile
    from .seal_orientation import prepare_rectangles

    # The temporary corrected page is used by ALL later local OCR routes,
    # including specialized SealOCR and optional secondary/audit models.
    with tempfile.TemporaryDirectory(prefix="seal-orientation-") as temp_dir:
        corrected, decisions = prepare_rectangles(source, regions, temp_dir)
        texts, artifacts = _recognize_oriented_seals(
            corrected, rows, regions, artifact_dir, artifact_url_prefix,
            ocr_backend, secondary_ocr_backend, requirement, footer_anchor_y,
            frozenset(index for index, decision in decisions.items()
                      if decision["applied_rotation"] == 180),
        )
        for artifact in artifacts:
            decision = decisions.get(artifact.get("index"))
            if decision is None:
                continue
            artifact["orientation"] = {key: value for key, value in decision.items()
                                       if key != "original_path"}
            if artifact_dir:
                original = Path(decision["original_path"])
                destination = Path(artifact_dir) / "seals" / original.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                copyfile(original, destination)
                artifact["orientation_original_url"] = f"{artifact_url_prefix.rstrip('/')}/seals/{original.name}"
                artifact["orientation_corrected_url"] = artifact.get("original_url", "")
        return texts, artifacts


def _recognize_oriented_seals(
    source, rows, regions, artifact_dir, artifact_url_prefix, ocr_backend,
    secondary_ocr_backend=None, requirement="", footer_anchor_y=None,
    orientation_resolved_indices=frozenset(),
):
    if ocr_backend == "paddle_seal":
        from .seal_dedicated import recognize_regions
        return recognize_regions(source, regions, artifact_dir, artifact_url_prefix)
    request = SealCropRequest(
        source=source,
        rows=rows,
        artifact_dir=artifact_dir,
        artifact_url_prefix=artifact_url_prefix,
        ocr_backend=ocr_backend,
        secondary_ocr_backend=secondary_ocr_backend,
        requirement=requirement,
        footer_anchor_y=footer_anchor_y,
        orientation_resolved_indices=orientation_resolved_indices,
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
        _apply_seal_audit_plan(request, collection, route)
        if route.should_server_audit and route.audit_backend:
            _rank_server_audit_candidates(request, collection, route)
            for audit_position, candidate in enumerate(
                route.ranked_candidates[:route.audit_limit]
            ):
                audit = SealAuditEvidence()
                _prepare_server_audit_images(
                    request, route, audit, candidate, audit_position
                )
                _read_server_audit_evidence(audit, route)
                _reconstruct_server_audit_evidence(
                    request, collection, route, audit, candidate
                )
                _record_server_audit_artifact(
                    request, collection, route, audit, candidate
                )
            _combine_overlapping_server_evidence(request, collection, route)
    return collection.texts, collection.artifacts


def _apply_seal_audit_plan(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
) -> None:
    """Resolve which provider may run the audit, and record an explicit skip.

    The audit used to hard-code ``paddle_server``.  Under a recognition plan the
    request scope is exactly the backend the user selected, so every audit call
    was denied and quietly returned ``[]`` -- the branch produced nothing while
    still looking like it had run.  Resolving the provider here keeps the
    decision visible and attributable.
    """
    plan = resolve_seal_audit(request.ocr_backend, allowed=allowed_providers())
    route.audit_mode = plan.mode
    route.audit_backend = plan.backend
    route.audit_band_backend = plan.band_backend
    route.audit_skip_reason = plan.skip_reason
    if route.should_server_audit and not plan.runs:
        # The route asked for a second reading and did not get one.  Record why
        # on every artifact, so a denied audit is visible instead of looking
        # like a stamp that simply had nothing to recover.
        for artifact in collection.artifacts:
            artifact["server_audit_mode"] = plan.mode
            artifact["server_audit_skipped_reason"] = plan.skip_reason
