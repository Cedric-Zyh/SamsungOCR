"""Route-specific confidence calculations with unchanged calibrated caps."""

from __future__ import annotations

from .seal_reference_constants import (
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE,
    BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
    BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS,
    BRANDED_STATION_MIN_INLIER_RATIO,
    CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
    COLOR_MASK_CONSENSUS_MIN_SCORE,
    COLOR_MASK_MIN_CORRELATION,
    COLOR_MASK_MIN_DICE,
    COLOR_MASK_MIN_SCORE,
    COLOR_SIFT_MIN_CORRELATION,
    COLOR_SIFT_MIN_DICE,
    COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
    COLOR_SIFT_MIN_INLIER_RATIO,
    COLOR_SIFT_MIN_SCORE,
    COLOR_SIFT_MIN_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
    COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO,
    CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    HIGH_SUPPORT_MIN_INLIER_RATIO,
    MIN_HOMOGRAPHY_INLIERS,
    MIN_INLIER_RATIO,
    MIN_SURFACE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_SCORE,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
    TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS,
    TRIMMED_CHROMATIC_MIN_INLIER_RATIO,
    ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    ULTRA_SUPPORT_MIN_INLIER_RATIO,
)


def _confidence_multi_reference_consensus(evidence: dict, consensus: dict | None) -> float:
    # Consensus has independent-file corroboration, but its leading match
    # is intentionally below the strict single-reference boundary.
    second_inliers = int(
        ((consensus or {}).get("matches") or [{}, {}])[1].get(
            "homography_inliers", CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        )
    ) if len((consensus or {}).get("matches") or []) >= 2 else 0
    confidence = min(
        0.94,
        0.91 + 0.01 * min(
            3.0,
            max(0, second_inliers - CONSENSUS_MIN_HOMOGRAPHY_INLIERS) / 5,
        ),
    )
    return confidence


def _confidence_company_conflict_ultra_reference(evidence: dict, consensus: dict | None) -> float:
    # The visual margin is exceptional, but this route still overrides a
    # contrary OCR company token.  Keep it below the strongest clean-text
    # strict matches and expose the conflict in the evidence record.
    confidence = min(
        0.96,
        0.93
        + 0.02 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
            ) / 60,
        )
        + 0.01 * min(
            1.0,
            max(
                0.0,
                float(evidence["inlier_ratio"])
                - COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO,
            ) / 0.15,
        ),
    )
    return confidence


def _confidence_receiving_one_glyph_reference(evidence: dict, consensus: dict | None) -> float:
    # A confirmed same-requirement reference and two broad geometric
    # representations outweigh exactly one OCR glyph substitution.  Keep
    # confidence below clean-text strict matches because the text conflict
    # remains visible for audit.
    confidence = min(
        0.96,
        0.94
        + 0.01 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
            ) / 30,
        )
        + 0.01 * min(
            1.0,
            max(
                0,
                int(evidence.get("chromatic_homography_inliers", 0))
                - RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
            ) / 30,
        ),
    )
    return confidence


def _confidence_bare_company_one_glyph_reference(evidence: dict, consensus: dict | None) -> float:
    # The bare company has no stamp-type anchor, so this confidence is
    # backed by two local-feature representations plus a registered
    # whole-ink mask, all against the exact same confirmed reference.
    confidence = min(
        0.96,
        0.94
        + 0.01 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
            ) / 30,
        )
        + 0.01 * min(
            1.0,
            max(
                0.0,
                float(evidence.get("color_mask_score", 0))
                - BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE,
            ) / 0.15,
        ),
    )
    return confidence


def _confidence_clipped_prefix_company_ultra_reference(evidence: dict, consensus: dict | None) -> float:
    # Exact long-suffix text plus two exceptionally strong geometric
    # representations can safely recover a short clipped company prefix.
    # Retain a small margin below certainty because the reference comes
    # from one independently confirmed file.
    confidence = min(
        0.97,
        0.95
        + 0.01 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
            ) / 40,
        )
        + 0.01 * min(
            1.0,
            max(
                0,
                int(evidence.get("chromatic_homography_inliers", 0))
                - CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
            ) / 40,
        ),
    )
    return confidence


def _confidence_high_purity_multi_reference_consensus(evidence: dict, consensus: dict | None) -> float:
    second_inliers = int(
        ((consensus or {}).get("matches") or [{}, {}])[1].get(
            "homography_inliers",
            HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
        )
    ) if len((consensus or {}).get("matches") or []) >= 2 else 0
    confidence = min(
        0.94,
        0.91 + 0.01 * min(
            3.0,
            max(
                0,
                second_inliers
                - HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
            ) / 5,
        ),
    )
    return confidence


def _confidence_chromatic_crop_multi_reference_consensus(evidence: dict, consensus: dict | None) -> float:
    second_inliers = int(
        ((consensus or {}).get("matches") or [{}, {}])[1].get(
            "homography_inliers",
            CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
        )
    ) if len((consensus or {}).get("matches") or []) >= 2 else 0
    confidence = min(
        0.94,
        0.91 + 0.01 * min(
            3.0,
            max(
                0,
                second_inliers
                - CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
            ) / 5,
        ),
    )
    return confidence


def _confidence_high_support_minor_coverage(evidence: dict, consensus: dict | None) -> float:
    confidence = min(
        0.96,
        0.92
        + 0.02 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
            ) / 60,
        )
        + 0.01 * min(
            1.0,
            max(
                0.0,
                float(evidence["inlier_ratio"])
                - HIGH_SUPPORT_MIN_INLIER_RATIO,
            ) / 0.20,
        ),
    )
    return confidence


def _confidence_ultra_support_partial_coverage(evidence: dict, consensus: dict | None) -> float:
    # Extra inliers/purity may raise confidence, while the small visible
    # surface prevents this route from making a near-certain claim.
    confidence = min(
        0.95,
        0.92
        + 0.02 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
            ) / 60,
        )
        + 0.01 * min(
            1.0,
            max(
                0.0,
                float(evidence["inlier_ratio"])
                - ULTRA_SUPPORT_MIN_INLIER_RATIO,
            ) / 0.20,
        ),
    )
    return confidence


def _confidence_trimmed_chromatic_single_reference(evidence: dict, consensus: dict | None) -> float:
    # This route has broad post-trim coverage and exceptionally pure
    # geometry, but still relies on one independently confirmed file.
    confidence = min(
        0.95,
        0.92
        + 0.02 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS,
            ) / 40,
        )
        + 0.01 * min(
            1.0,
            max(
                0.0,
                float(evidence["inlier_ratio"])
                - TRIMMED_CHROMATIC_MIN_INLIER_RATIO,
            ) / 0.15,
        ),
    )
    return confidence


def _confidence_branded_station_single_reference(evidence: dict, consensus: dict | None) -> float:
    # The semantic stamp family and seven-digit station structure are
    # independent of SIFT.  Keep the one-reference result below the
    # strongest generic matches even when its geometric margin grows.
    confidence = min(
        0.95,
        0.92
        + 0.02 * min(
            1.0,
            max(
                0,
                int(evidence["homography_inliers"])
                - BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS,
            ) / 40,
        )
        + 0.01 * min(
            1.0,
            max(
                0.0,
                float(evidence["inlier_ratio"])
                - BRANDED_STATION_MIN_INLIER_RATIO,
            ) / 0.15,
        ),
    )
    return confidence


def _confidence_color_mask_multi_reference_consensus(evidence: dict, consensus: dict | None) -> float:
    # Two independent confirmed files compensate for low local-feature
    # purity caused by table-line crossings. Keep this below clean OCR and
    # strict SIFT routes because only a short company fragment survived.
    second_score = float(
        ((consensus or {}).get("matches") or [{}, {}])[1].get(
            "color_mask_score", COLOR_MASK_CONSENSUS_MIN_SCORE
        )
    ) if len((consensus or {}).get("matches") or []) >= 2 else 0.0
    confidence = min(
        0.94,
        0.92 + 0.20 * max(
            0.0, second_score - COLOR_MASK_CONSENSUS_MIN_SCORE
        ),
    )
    return confidence


def _confidence_strong_prefix_color_mask_multi_reference_consensus(evidence: dict, consensus: dict | None) -> float:
    # A seven-character exact OCR prefix provides stronger semantics than
    # the short-fragment company route, while the visual threshold remains
    # deliberately modest. Keep the result at 92% unless both votes clear
    # their floor by a meaningful margin.
    matches = (consensus or {}).get("matches") or []
    second_score = float(matches[1].get(
        "color_mask_score", PREFIX_COLOR_CONSENSUS_MIN_SCORE
    )) if len(matches) >= 2 else 0.0
    confidence = min(
        0.94,
        0.92 + 0.20 * max(
            0.0, second_score - PREFIX_COLOR_CONSENSUS_MIN_SCORE
        ),
    )
    return confidence


def _confidence_color_mask_geometry(evidence: dict, consensus: dict | None) -> float:
    # Whole-ink agreement is deliberately independent of sparse SIFT
    # keypoints. It starts at 91% and gains only a small margin above the
    # three strict 0.80 boundaries.
    margin = min(
        float(evidence.get("color_mask_score", 0)) - COLOR_MASK_MIN_SCORE,
        float(evidence.get("color_mask_correlation", 0))
        - COLOR_MASK_MIN_CORRELATION,
        float(evidence.get("color_mask_dice", 0)) - COLOR_MASK_MIN_DICE,
    )
    confidence = min(0.96, 0.91 + 0.5 * max(0.0, margin))
    return confidence


def _confidence_color_mask_sift_geometry(evidence: dict, consensus: dict | None) -> float:
    # Both representations agree on the same pair. Keep this conservative
    # route below the strongest strict SIFT/color-only matches.
    color_margin = min(
        float(evidence.get("color_mask_score", 0)) - COLOR_SIFT_MIN_SCORE,
        float(evidence.get("color_mask_correlation", 0))
        - COLOR_SIFT_MIN_CORRELATION,
        float(evidence.get("color_mask_dice", 0)) - COLOR_SIFT_MIN_DICE,
    )
    sift_margin = min(
        (int(evidence.get("homography_inliers", 0))
         - COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS) / 80,
        (float(evidence.get("inlier_ratio", 0))
         - COLOR_SIFT_MIN_INLIER_RATIO) / 0.25,
        (min(
            float(evidence.get("candidate_coverage", 0)),
            float(evidence.get("reference_coverage", 0)),
        ) - COLOR_SIFT_MIN_SURFACE_COVERAGE) / 0.30,
    )
    confidence = min(
        0.95,
        0.91 + 0.20 * max(0.0, color_margin)
        + 0.02 * max(0.0, sift_margin),
    )
    return confidence


_CONFIDENCE_ROUTES = {
    'multi_reference_consensus': _confidence_multi_reference_consensus,
    'company_conflict_ultra_reference': _confidence_company_conflict_ultra_reference,
    'receiving_one_glyph_reference': _confidence_receiving_one_glyph_reference,
    'bare_company_one_glyph_reference': _confidence_bare_company_one_glyph_reference,
    'clipped_prefix_company_ultra_reference': _confidence_clipped_prefix_company_ultra_reference,
    'high_purity_multi_reference_consensus': _confidence_high_purity_multi_reference_consensus,
    'chromatic_crop_multi_reference_consensus': _confidence_chromatic_crop_multi_reference_consensus,
    'high_support_minor_coverage': _confidence_high_support_minor_coverage,
    'ultra_support_partial_coverage': _confidence_ultra_support_partial_coverage,
    'trimmed_chromatic_single_reference': _confidence_trimmed_chromatic_single_reference,
    'branded_station_single_reference': _confidence_branded_station_single_reference,
    'color_mask_multi_reference_consensus': _confidence_color_mask_multi_reference_consensus,
    'strong_prefix_color_mask_multi_reference_consensus': _confidence_strong_prefix_color_mask_multi_reference_consensus,
    'color_mask_geometry': _confidence_color_mask_geometry,
    'color_mask_sift_geometry': _confidence_color_mask_sift_geometry,
}


def _reference_confidence(
    evidence: dict, route: str, consensus: dict | None = None
) -> float:
    # Passing the strict gate is already strong human-reference evidence.
    # The remaining margin raises confidence gradually without ever claiming
    # certainty from a visual template alone.
    inlier_margin = min(
        1.0,
        (int(evidence["homography_inliers"]) - MIN_HOMOGRAPHY_INLIERS) / 120,
    )
    ratio_margin = min(
        1.0,
        (float(evidence["inlier_ratio"]) - MIN_INLIER_RATIO) / 0.25,
    )
    coverage_margin = min(
        1.0,
        (
            min(
                float(evidence["candidate_coverage"]),
                float(evidence["reference_coverage"]),
            )
            - MIN_SURFACE_COVERAGE
        ) / 0.35,
    )
    confidence = round(
        min(0.98, 0.90 + 0.04 * inlier_margin + 0.02 * ratio_margin
            + 0.02 * coverage_margin),
        3,
    )
    calculator = _CONFIDENCE_ROUTES.get(route)
    if calculator is not None:
        confidence = calculator(evidence, consensus)
    return round(confidence, 3)
