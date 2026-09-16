"""Pure acceptance gates and evidence ranking for visual seal references."""

from __future__ import annotations

import re
from .parser import normalize_text
from .seal_reference_constants import (
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES,
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO,
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE,
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_CORRELATION,
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_DICE,
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE,
    BARE_COMPANY_ONE_GLYPH_MIN_COMPANY_SCORE,
    BARE_COMPANY_ONE_GLYPH_MIN_GOOD_MATCHES,
    BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
    BARE_COMPANY_ONE_GLYPH_MIN_INLIER_RATIO,
    BARE_COMPANY_ONE_GLYPH_MIN_SURFACE_COVERAGE,
    BRANDED_STATION_MIN_GOOD_MATCHES,
    BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS,
    BRANDED_STATION_MIN_INLIER_RATIO,
    BRANDED_STATION_MIN_SURFACE_COVERAGE,
    CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES,
    CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CHROMATIC_CONSENSUS_MIN_INLIER_RATIO,
    CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MAX_MISSING_PREFIX,
    CLIPPED_COMPANY_REFERENCE_MIN_CANDIDATE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_CANDIDATE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_INLIER_RATIO,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_REFERENCE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MIN_COMPANY_SCORE,
    CLIPPED_COMPANY_REFERENCE_MIN_GOOD_MATCHES,
    CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
    CLIPPED_COMPANY_REFERENCE_MIN_INLIER_RATIO,
    CLIPPED_COMPANY_REFERENCE_MIN_RECOGNIZED_LENGTH,
    CLIPPED_COMPANY_REFERENCE_MIN_REFERENCE_COVERAGE,
    COLOR_MASK_CONSENSUS_MIN_CANDIDATE_COVERAGE,
    COLOR_MASK_CONSENSUS_MIN_CORRELATION,
    COLOR_MASK_CONSENSUS_MIN_DICE,
    COLOR_MASK_CONSENSUS_MIN_GOOD_MATCHES,
    COLOR_MASK_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    COLOR_MASK_CONSENSUS_MIN_RECOGNIZED_CHARS,
    COLOR_MASK_CONSENSUS_MIN_REFERENCE_COVERAGE,
    COLOR_MASK_CONSENSUS_MIN_SCORE,
    COLOR_MASK_MIN_CORRELATION,
    COLOR_MASK_MIN_DICE,
    COLOR_MASK_MIN_SCORE,
    COLOR_SIFT_MIN_CORRELATION,
    COLOR_SIFT_MIN_DICE,
    COLOR_SIFT_MIN_GOOD_MATCHES,
    COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
    COLOR_SIFT_MIN_INLIER_RATIO,
    COLOR_SIFT_MIN_SCORE,
    COLOR_SIFT_MIN_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE,
    COMPANY_CONFLICT_REFERENCE_MIN_GOOD_MATCHES,
    COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
    COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO,
    COMPANY_CONFLICT_REFERENCE_MIN_SHARED_FRAGMENT,
    COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE,
    CONSENSUS_MIN_GOOD_MATCHES,
    CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CONSENSUS_MIN_INLIER_RATIO,
    CONSENSUS_MIN_SURFACE_COVERAGE,
    HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES,
    HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO,
    HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE,
    HIGH_RATIO_MIN_GOOD_MATCHES,
    HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS,
    HIGH_RATIO_MIN_INLIER_RATIO,
    HIGH_RATIO_MIN_SURFACE_COVERAGE,
    HIGH_SUPPORT_MIN_GOOD_MATCHES,
    HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    HIGH_SUPPORT_MIN_INLIER_RATIO,
    HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
    MIN_GOOD_MATCHES,
    MIN_HOMOGRAPHY_INLIERS,
    MIN_INLIER_RATIO,
    MIN_SURFACE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_CANDIDATE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_CORRELATION,
    PREFIX_COLOR_CONSENSUS_MIN_DICE,
    PREFIX_COLOR_CONSENSUS_MIN_GOOD_MATCHES,
    PREFIX_COLOR_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    PREFIX_COLOR_CONSENSUS_MIN_INLIER_RATIO,
    PREFIX_COLOR_CONSENSUS_MIN_REFERENCE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_SCORE,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE,
    RECEIVING_ONE_GLYPH_MIN_COMPANY_SCORE,
    RECEIVING_ONE_GLYPH_MIN_GOOD_MATCHES,
    RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
    RECEIVING_ONE_GLYPH_MIN_INLIER_RATIO,
    RECEIVING_ONE_GLYPH_MIN_SURFACE_COVERAGE,
    TRIMMED_CHROMATIC_MIN_GOOD_MATCHES,
    TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS,
    TRIMMED_CHROMATIC_MIN_INLIER_RATIO,
    TRIMMED_CHROMATIC_MIN_SURFACE_COVERAGE,
    ULTRA_SUPPORT_MIN_GOOD_MATCHES,
    ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    ULTRA_SUPPORT_MIN_INLIER_RATIO,
    ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
)


def _evidence_rank(evidence: dict) -> tuple:
    return (
        int(evidence.get("homography_inliers", 0)),
        min(
            float(evidence.get("candidate_coverage", 0)),
            float(evidence.get("reference_coverage", 0)),
        ),
        int(evidence.get("good_matches", 0)),
    )


def _color_evidence_rank(evidence: dict) -> tuple:
    return (
        float(evidence.get("color_mask_score", 0)),
        float(evidence.get("color_mask_correlation", 0)),
        float(evidence.get("color_mask_dice", 0)),
    )


def _trimmed_chromatic_evidence_rank(evidence: dict) -> tuple:
    return (
        int(evidence.get("trimmed_chromatic_homography_inliers", 0)),
        min(
            float(evidence.get("trimmed_chromatic_candidate_coverage", 0)),
            float(evidence.get("trimmed_chromatic_reference_coverage", 0)),
        ),
        int(evidence.get("trimmed_chromatic_good_matches", 0)),
    )


def _passes_strict_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= MIN_GOOD_MATCHES
        and evidence["homography_inliers"] >= MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= MIN_INLIER_RATIO
        and evidence["candidate_coverage"] >= MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"] >= MIN_SURFACE_COVERAGE
    )


def _passes_high_ratio_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= HIGH_RATIO_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= HIGH_RATIO_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= HIGH_RATIO_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= HIGH_RATIO_MIN_SURFACE_COVERAGE
    )


def _passes_high_support_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= HIGH_SUPPORT_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= HIGH_SUPPORT_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= HIGH_SUPPORT_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= HIGH_SUPPORT_MIN_SURFACE_COVERAGE
    )


def _passes_ultra_support_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= ULTRA_SUPPORT_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= ULTRA_SUPPORT_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= ULTRA_SUPPORT_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= ULTRA_SUPPORT_MIN_SURFACE_COVERAGE
    )


def _passes_trimmed_chromatic_gate(evidence: dict) -> bool:
    return bool(
        int(evidence.get("trimmed_chromatic_good_matches", 0))
        >= TRIMMED_CHROMATIC_MIN_GOOD_MATCHES
        and int(evidence.get("trimmed_chromatic_homography_inliers", 0))
        >= TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("trimmed_chromatic_inlier_ratio", 0))
        >= TRIMMED_CHROMATIC_MIN_INLIER_RATIO
        and float(evidence.get("trimmed_chromatic_candidate_coverage", 0))
        >= TRIMMED_CHROMATIC_MIN_SURFACE_COVERAGE
        and float(evidence.get("trimmed_chromatic_reference_coverage", 0))
        >= TRIMMED_CHROMATIC_MIN_SURFACE_COVERAGE
    )


def _passes_branded_station_gate(
    evidence: dict,
    *,
    requirement: str,
    recognized: str,
) -> bool:
    """Accept only the numbered Samsung pickup-stamp reference shape."""
    normalized_requirement = normalize_text(requirement)
    normalized_recognized = normalize_text(recognized)
    if not re.fullmatch(
        r"三星电子服务中心取机专用章\d{7}站",
        normalized_requirement,
    ):
        return False
    if "三星电子服务中心" not in normalized_recognized:
        return False
    return bool(
        int(evidence.get("good_matches", 0))
        >= BRANDED_STATION_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= BRANDED_STATION_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= BRANDED_STATION_MIN_SURFACE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= BRANDED_STATION_MIN_SURFACE_COVERAGE
    )


def _longest_common_substring_length(left: str, right: str) -> int:
    """Return the longest contiguous shared fragment length."""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for left_character in left:
        current = [0]
        for index, right_character in enumerate(right, start=1):
            value = (
                previous[index - 1] + 1
                if left_character == right_character else 0
            )
            current.append(value)
            best = max(best, value)
        previous = current
    return best


def _passes_company_conflict_reference_gate(
    evidence: dict, seal_check: dict,
) -> bool:
    """Override one OCR conflict only with ultra-strong independent evidence."""
    if seal_check.get("company_conflict") is not True:
        return False
    requirement = normalize_text(str(seal_check.get("requirement") or ""))
    recognized = normalize_text(" ".join([
        str(seal_check.get("recognized") or ""),
        *[
            str(value)
            for value in (seal_check.get("all_recognized") or [])
        ],
    ]))
    specific_type = "售后专用章"
    if (
        not requirement.endswith(specific_type)
        or specific_type not in recognized
    ):
        return False
    required_company = requirement[: -len(specific_type)]
    shared_fragment = _longest_common_substring_length(
        required_company, recognized
    )
    return bool(
        float(seal_check.get("company_score", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE
        and shared_fragment
        >= COMPANY_CONFLICT_REFERENCE_MIN_SHARED_FRAGMENT
        and int(evidence.get("good_matches", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE
        and int(evidence.get("chromatic_good_matches", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES
        and int(evidence.get("chromatic_homography_inliers", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
        and float(evidence.get("chromatic_inlier_ratio", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO
        and float(evidence.get("chromatic_candidate_coverage", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE
        and float(evidence.get("chromatic_reference_coverage", 0))
        >= COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE
    )


def _passes_receiving_one_glyph_reference_gate(
    evidence: dict, seal_check: dict,
) -> bool:
    """Recover one substituted company glyph only with dual strong geometry."""
    if seal_check.get("company_conflict") is not True:
        return False
    specific_type = "收货专用章"
    requirement = normalize_text(str(seal_check.get("requirement") or ""))
    if not requirement.endswith(specific_type):
        return False
    required_company = requirement[: -len(specific_type)]
    if not required_company.endswith(("有限公司", "有限责任公司")):
        return False
    fragments = [
        normalize_text(str(seal_check.get("recognized") or "")),
        *[
            normalize_text(str(value))
            for value in (seal_check.get("all_recognized") or [])
        ],
    ]
    if not any(specific_type in fragment for fragment in fragments):
        return False
    if not _has_exactly_one_company_glyph_substitution(
        required_company, fragments
    ):
        return False
    return bool(
        float(seal_check.get("company_score", 0))
        >= RECEIVING_ONE_GLYPH_MIN_COMPANY_SCORE
        and int(evidence.get("good_matches", 0))
        >= RECEIVING_ONE_GLYPH_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= RECEIVING_ONE_GLYPH_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= RECEIVING_ONE_GLYPH_MIN_SURFACE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= RECEIVING_ONE_GLYPH_MIN_SURFACE_COVERAGE
        and int(evidence.get("chromatic_good_matches", 0))
        >= RECEIVING_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES
        and int(evidence.get("chromatic_homography_inliers", 0))
        >= RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
        and float(evidence.get("chromatic_inlier_ratio", 0))
        >= RECEIVING_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO
        and float(evidence.get("chromatic_candidate_coverage", 0))
        >= RECEIVING_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE
        and float(evidence.get("chromatic_reference_coverage", 0))
        >= RECEIVING_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE
    )


def _has_exactly_one_company_glyph_substitution(
    required_company: str, fragments: list[str],
) -> bool:
    """Require an equal-length legal company with one positional mismatch."""
    legal_suffixes = ("有限公司", "有限责任公司")
    return any(
        len(fragment) == len(required_company)
        and fragment.endswith(legal_suffixes)
        and sum(
            left != right
            for left, right in zip(required_company, fragment, strict=True)
        ) == 1
        for fragment in fragments
    )


def _passes_bare_company_one_glyph_reference_gate(
    evidence: dict, seal_check: dict,
) -> bool:
    """Override one bare-company OCR glyph only with three visual agreements."""
    if seal_check.get("company_conflict") is not True:
        return False
    requirement = normalize_text(str(seal_check.get("requirement") or ""))
    if not re.fullmatch(
        r"[\u4e00-\u9fffA-Za-z0-9（）()·]+(?:有限责任公司|有限公司)",
        requirement,
    ):
        return False
    fragments = [
        normalize_text(str(seal_check.get("recognized") or "")),
        *[
            normalize_text(str(value))
            for value in (seal_check.get("all_recognized") or [])
        ],
    ]
    if not _has_exactly_one_company_glyph_substitution(
        requirement, fragments
    ):
        return False
    return bool(
        float(seal_check.get("company_score", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_COMPANY_SCORE
        and int(evidence.get("good_matches", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_SURFACE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_SURFACE_COVERAGE
        and int(evidence.get("chromatic_good_matches", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES
        and int(evidence.get("chromatic_homography_inliers", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
        and float(evidence.get("chromatic_inlier_ratio", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO
        and float(evidence.get("chromatic_candidate_coverage", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE
        and float(evidence.get("chromatic_reference_coverage", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE
        and float(evidence.get("color_mask_score", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_CORRELATION
        and float(evidence.get("color_mask_dice", 0))
        >= BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_DICE
    )


def _passes_clipped_company_reference_gate(
    evidence: dict, seal_check: dict,
) -> bool:
    """Accept a bare company seal only when OCR lost a short exact prefix."""
    if seal_check.get("company_conflict") is not True:
        return False
    requirement = normalize_text(str(seal_check.get("requirement") or ""))
    recognized = normalize_text(str(seal_check.get("recognized") or ""))
    legal_suffixes = ("有限公司", "有限责任公司")
    if (
        not requirement.endswith(legal_suffixes)
        or not recognized.endswith(legal_suffixes)
        or len(recognized) < CLIPPED_COMPANY_REFERENCE_MIN_RECOGNIZED_LENGTH
        or not requirement.endswith(recognized)
    ):
        return False
    missing_prefix = len(requirement) - len(recognized)
    if not 1 <= missing_prefix <= CLIPPED_COMPANY_REFERENCE_MAX_MISSING_PREFIX:
        return False
    return bool(
        float(seal_check.get("company_score", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_COMPANY_SCORE
        and int(evidence.get("good_matches", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_CANDIDATE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_REFERENCE_COVERAGE
        and int(evidence.get("chromatic_good_matches", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES
        and int(evidence.get("chromatic_homography_inliers", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
        and float(evidence.get("chromatic_inlier_ratio", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_INLIER_RATIO
        and float(evidence.get("chromatic_candidate_coverage", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_CANDIDATE_COVERAGE
        and float(evidence.get("chromatic_reference_coverage", 0))
        >= CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_REFERENCE_COVERAGE
    )


def _passes_color_mask_gate(evidence: dict) -> bool:
    return bool(
        float(evidence.get("color_mask_score", 0)) >= COLOR_MASK_MIN_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= COLOR_MASK_MIN_CORRELATION
        and float(evidence.get("color_mask_dice", 0)) >= COLOR_MASK_MIN_DICE
    )


def _passes_color_sift_gate(evidence: dict) -> bool:
    """Require whole-ink and local geometry on the exact same reference."""
    return bool(
        float(evidence.get("color_mask_score", 0)) >= COLOR_SIFT_MIN_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= COLOR_SIFT_MIN_CORRELATION
        and float(evidence.get("color_mask_dice", 0)) >= COLOR_SIFT_MIN_DICE
        and int(evidence.get("good_matches", 0))
        >= COLOR_SIFT_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= COLOR_SIFT_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= COLOR_SIFT_MIN_SURFACE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= COLOR_SIFT_MIN_SURFACE_COVERAGE
    )


def _is_bare_company_requirement(value: str) -> bool:
    return bool(re.fullmatch(
        r"[\u4e00-\u9fffA-Za-z0-9（）()·]+(?:有限责任公司|有限公司)",
        value,
    ))


def _recognized_requirement_subsequence_length(
    recognized: str, requirement: str,
) -> int:
    """Count OCR characters that occur in requirement order, ignoring noise."""
    required_chars = [char for char in requirement if "\u4e00" <= char <= "\u9fff"]
    observed_chars = [char for char in recognized if "\u4e00" <= char <= "\u9fff"]
    position = 0
    matched = 0
    for char in observed_chars:
        try:
            offset = required_chars.index(char, position)
        except ValueError:
            continue
        matched += 1
        position = offset + 1
    return matched


def _has_independent_short_company_fragment(
    seal_check: dict, requirement: str,
) -> bool:
    fragments = [
        normalize_text(str(seal_check.get("recognized") or "")),
        *[
            normalize_text(str(value))
            for value in (seal_check.get("all_recognized") or [])
        ],
    ]
    for fragment in fragments:
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,6}", fragment):
            continue
        if (
            _recognized_requirement_subsequence_length(fragment, requirement)
            == len(fragment)
            and len(fragment) >= COLOR_MASK_CONSENSUS_MIN_RECOGNIZED_CHARS
        ):
            return True
    return False


def _passes_color_mask_consensus_vote(evidence: dict) -> bool:
    return bool(
        float(evidence.get("color_mask_score", 0))
        >= COLOR_MASK_CONSENSUS_MIN_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= COLOR_MASK_CONSENSUS_MIN_CORRELATION
        and float(evidence.get("color_mask_dice", 0))
        >= COLOR_MASK_CONSENSUS_MIN_DICE
        and int(evidence.get("good_matches", 0))
        >= COLOR_MASK_CONSENSUS_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= COLOR_MASK_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("candidate_coverage", 0))
        >= COLOR_MASK_CONSENSUS_MIN_CANDIDATE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= COLOR_MASK_CONSENSUS_MIN_REFERENCE_COVERAGE
    )


def _recognized_requirement_prefix_length(
    seal_check: dict, requirement: str,
) -> int:
    """Return the longest independently OCR-read exact requirement prefix."""
    fragments = [
        normalize_text(str(seal_check.get("recognized") or "")),
        *[
            normalize_text(str(value))
            for value in (seal_check.get("all_recognized") or [])
        ],
    ]
    return max(
        (
            len(fragment)
            for fragment in fragments
            if re.fullmatch(r"[\u4e00-\u9fff]+", fragment)
            and requirement.startswith(fragment)
        ),
        default=0,
    )


def _passes_prefix_color_mask_consensus_vote(evidence: dict) -> bool:
    return bool(
        float(evidence.get("color_mask_score", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_CORRELATION
        and float(evidence.get("color_mask_dice", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_DICE
        and int(evidence.get("good_matches", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_CANDIDATE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= PREFIX_COLOR_CONSENSUS_MIN_REFERENCE_COVERAGE
    )


def _passes_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"] >= CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"] >= CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"] >= CONSENSUS_MIN_SURFACE_COVERAGE
    )


def _passes_high_purity_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"]
        >= HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"]
        >= HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE
    )


def _passes_chromatic_crop_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"]
        >= CHROMATIC_CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE
    )
