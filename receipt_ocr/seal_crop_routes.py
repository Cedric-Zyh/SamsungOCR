"""Explicit planning rules for bounded color-safe Server seal audits."""

from __future__ import annotations

import re

from .parser import compare_seal_text, normalize_text
from .seal_rules import (
    _company_conflict_allows_clipped_prefix_server_recheck,
    _has_shared_specific_stamp_type,
    _region_overlap_over_smaller,
    _strong_unread_colored_stamp_route,
)
from .seal_crop_types import SealCropRequest, SealEvidenceCollection, SealAuditRoute


def _plan_server_audit_route(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
) -> None:
    """Preserve the original evidence thresholds and provider exclusions."""
    route.clipped_prefix_server_recheck = bool(
        route.preliminary_company_conflict
        and _company_conflict_allows_clipped_prefix_server_recheck(
            request.requirement, collection.texts
        )
    )
    route.explicit_stamp_type = any(
        token in request.requirement
        for token in ("专用章", "维修专章", "收货章", "仓储部", "维修中心")
    )
    route.branch_stamp_rotation_route = bool(
        "分公司" in normalize_text(request.requirement)
        and route.explicit_stamp_type
        and float(route.preliminary.get("score", 0)) >= 0.50
    )
    shared_specific_stamp_type = _has_shared_specific_stamp_type(request.requirement, collection.texts)
    # A long service-station number is highly discriminative and is
    # often the easiest part of a faint rectangular stamp for the
    # Server model to recover.  Permit a color-only audit even when
    # Mobile/Vision similarity is low; the normal matcher still needs
    # both enough stamp text and the complete number before it can
    # become reliable.  This does not apply to generic company or
    # stamp-type requirements.
    route.numbered_service_stamp = bool(
        any(token in request.requirement for token in ("维修中心", "服务中心"))
        and re.search(r"\d{6,}", request.requirement)
    )
    named_mobile_contact = bool(
        re.fullmatch(
            r"[\u4e00-\u9fff]{2,8}1\d{10}",
            normalize_text(request.requirement),
        )
    )
    samsung_authorized_store = bool(
        "三星授权体验店" in request.requirement
        and float(route.preliminary.get("score", 0)) >= 0.25
    )
    # Two common customer-master templates do not end in a legal
    # company suffix and often have no explicit ``专用章`` token:
    # ``...服务总汇`` and ``...客户服务中心``.  Their pale circular
    # stamps can leave Mobile/Vision just below the reliable matcher
    # boundary even though the crop contains a long distinctive
    # organization name.  Route only already-similar candidates to
    # the color-only Server audit.  The final matcher thresholds and
    # complete-company conflict guard stay unchanged, so this merely
    # adds evidence and cannot force a pass by itself.
    service_organization = bool(
        any(token in request.requirement for token in ("服务总汇", "客户服务中心"))
        and len(normalize_text(request.requirement)) >= 10
        and float(route.preliminary.get("score", 0)) >= 0.50
    )
    route.dense_partitioned_service_organization = bool(
        re.fullmatch(
            r"[\u4e00-\u9fff]+",
            normalize_text(request.requirement),
        )
        and len(normalize_text(request.requirement)) >= 10
        and normalize_text(request.requirement).endswith("服务中心")
        and float(route.preliminary.get("score", 0)) <= 0.20
        and max(
            (
                float(candidate.get("pixel_ratio", 0))
                for candidate in collection.server_candidates
            ),
            default=0.0,
        )
        >= 0.20
    )
    strong_unread_colored_stamp = _strong_unread_colored_stamp_route(
        request.requirement,
        float(route.preliminary.get("score", 0)),
        max(
            (
                float(candidate.get("pixel_ratio", 0))
                for candidate in collection.server_candidates
            ),
            default=0.0,
        ),
    )
    route.robust_round_shop = bool(
        request.ocr_backend == "vision"
        and request.secondary_ocr_backend == "paddle"
        and not route.preliminary_company_conflict
        and normalize_text(request.requirement).endswith("商店")
        and len(normalize_text(request.requirement)) >= 10
        and float(route.preliminary.get("score", 0)) <= 0.50
        and max(
            (
                float(candidate.get("pixel_ratio", 0))
                for candidate in collection.server_candidates
            ),
            default=0.0,
        )
        >= 0.08
    )
    route.fragmented_local_service_center = bool(
        re.fullmatch(
            r"三星电子[\u4e00-\u9fff]{2,6}服务中心",
            normalize_text(request.requirement),
        )
    )
    route.should_server_audit = bool(
        request.requirement
        and not route.preliminary.get("reliable")
        and (
            any(
                request.requirement.endswith(suffix)
                for suffix in ("有限公司", "有限责任公司", "分公司")
            )
            or (route.explicit_stamp_type and float(route.preliminary.get("score", 0)) >= 0.50)
            or shared_specific_stamp_type
            or route.numbered_service_stamp
            or named_mobile_contact
            or samsung_authorized_store
            or service_organization
            or strong_unread_colored_stamp
            or route.robust_round_shop
        )
        and request.ocr_backend != "paddle_server"
        and request.secondary_ocr_backend != "paddle_server"
    )


def _rank_server_audit_candidates(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
) -> None:
    """Rank candidates and retain the original one-or-two-region audit limit."""
    # The circular/oval unwrapped image was the only transform
    # that improved reviewed pale-company stamps. Audit just the
    # most similar region instead of every transform of every
    # region, keeping batch latency bounded.
    route.ranked_candidates = sorted(
        collection.server_candidates,
        key=lambda candidate: compare_seal_text(
            request.requirement, candidate["evidence"]
        ).get("score", 0),
        reverse=True,
    )
    route.overlapping_repair_route = bool(
        normalize_text(request.requirement).endswith(("维修专章", "维修专用章"))
        and any(
            first["region"].role == "收货客户章"
            and second["region"].role == "收货客户章"
            and first["region"].color == second["region"].color
            and _region_overlap_over_smaller(first["region"], second["region"])
            >= 0.35
            for first_index, first in enumerate(route.ranked_candidates)
            for second in route.ranked_candidates[first_index + 1 :]
        )
    )
    # A heavy rectangular station stamp can be split into upper
    # and lower boxes by table lines. Mobile may rank the numeric
    # half first even though Server recovers the complete text
    # from the other half. Audit at most two color-only boxes for
    # this narrowly identified type; all other requirements keep
    # the original one-candidate latency bound.
    route.company_only_requirement = any(
        request.requirement.endswith(suffix)
        for suffix in ("有限公司", "有限责任公司", "分公司")
    )
    # Multiple overlapping customer stamps can be split into two
    # similarly ranked oval candidates.  For a pure company-name
    # requirement, audit both top color-only derivatives: they
    # cannot contain the black printed requirement and therefore
    # remain safe from self-matching.  This recovered candidate
    # is still subject to the normal reliable-company threshold.
    route.audit_limit = (
        2
        if (
            route.numbered_service_stamp
            or route.company_only_requirement
            or route.overlapping_repair_route
            # One overlapping round detection can preserve the
            # center type while another preserves the company
            # arc. Both derivatives contain colored ink only;
            # final company/type conflict guards stay unchanged.
            or (
                route.explicit_stamp_type
                and float(route.preliminary.get("score", 0)) >= 0.72
                and len(route.ranked_candidates) > 1
            )
        )
        else 1
    )
    collection.overlapping_server_audits: list[dict] = []
