"""Calibrated visual-reference thresholds and their audit representation."""

from __future__ import annotations



# These boundaries were selected against the complete 301-image human-truth
# set.  The two known wrong-stamp controls have 56/24 geometric inliers; the
# first accepted true stamp has 81.  Keep a visible margin instead of tuning
# to the last positive sample.
MIN_GOOD_MATCHES = 110
MIN_HOMOGRAPHY_INLIERS = 80
MIN_INLIER_RATIO = 0.65
MIN_SURFACE_COVERAGE = 0.30

# A narrowly lower match-count route is allowed only when RANSAC purity is
# materially higher.  This covers small crop/preprocessing jitter without
# weakening the original route: at least 80 correspondences must still be
# geometric inliers and at least 70% of all candidate matches must agree.
HIGH_RATIO_MIN_GOOD_MATCHES = 105
HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS = 80
HIGH_RATIO_MIN_INLIER_RATIO = 0.70
HIGH_RATIO_MIN_SURFACE_COVERAGE = 0.30

# Very dense, high-purity geometry can tolerate a tiny crop-boundary wobble.
# This is not a general coverage relaxation: both match and inlier counts are
# substantially above the normal route and the 28% floor still excludes the
# next high-inlier sample (24.4% candidate coverage).
HIGH_SUPPORT_MIN_GOOD_MATCHES = 130
HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS = 100
HIGH_SUPPORT_MIN_INLIER_RATIO = 0.70
HIGH_SUPPORT_MIN_SURFACE_COVERAGE = 0.28

# A partially obscured/cropped stamp may fall just below the 28% surface
# boundary even though the remaining ink carries exceptionally dense and
# pure geometry.  The complete 301-image human-truth matrix has exactly one
# new positive at 152/107 matches/inliers, 70.39% purity and 24.40%/51.10%
# bidirectional coverage.  The strongest known wrong-stamp control has only
# 46/24 matches/inliers.  Keep this as an independent all-boundaries gate;
# it must never act as a general relaxation of the existing coverage routes.
ULTRA_SUPPORT_MIN_GOOD_MATCHES = 145
ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS = 105
ULTRA_SUPPORT_MIN_INLIER_RATIO = 0.70
ULTRA_SUPPORT_MIN_SURFACE_COVERAGE = 0.24

# A second route can recover a repeated stamp that narrowly misses the
# single-reference inlier boundary.  It deliberately needs two *different*
# human-confirmed files to agree with the same detected candidate region.
# The leading reference still needs a visible margin over the strongest
# known wrong-stamp control (56 inliers), and every vote must retain the same
# ratio/coverage guarantees as the strict route.
CONSENSUS_MIN_DISTINCT_REFERENCES = 2
CONSENSUS_MIN_TOP_INLIERS = 75
CONSENSUS_MIN_GOOD_MATCHES = 98
CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 65
CONSENSUS_MIN_INLIER_RATIO = 0.65
CONSENSUS_MIN_SURFACE_COVERAGE = 0.30

# Two independent human-confirmed files may safely compensate for a modestly
# lower correspondence count when *each* vote is geometrically purer than the
# standard consensus route.  The 301-image matrix has one pending positive
# with 90/72 and 89/66 matches/inliers, 80.0%/74.16% purity and at least 33%
# bidirectional coverage.  Requiring two distinct files prevents repeated
# crops from one scan from masquerading as independent corroboration.
HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES = 2
HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS = 70
HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES = 85
HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 65
HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO = 0.70
HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE = 0.30

# Black form lines and signatures sometimes survive the color-isolation stage
# and enlarge the grayscale SIFT crop even when the stamp itself is complete.
# A second representation derives crop bounds from chromatic ink but retains
# the original grayscale texture for SIFT.  It may promote only when two
# distinct human-confirmed files agree; the leading vote must be exceptionally
# pure and dense, while the second still carries substantial independent
# support.  The full 301-image matrix accepts one positive and no wrong stamp.
CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES = 2
CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES = 100
CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 65
CHROMATIC_CONSENSUS_MIN_INLIER_RATIO = 0.68
CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE = 0.24
CHROMATIC_CONSENSUS_MIN_TOP_INLIERS = 95
CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO = 0.85

# A few isolated colored scanner specks can stretch an otherwise correct
# stamp crop far beyond the seal surface.  A second chromatic representation
# trims only the outermost 2% of colored pixels on each axis.  It may promote
# a single confirmed reference only when the remaining geometry is both very
# pure and broad.  On the complete 301-image truth matrix the recovered
# positive starts at 94/77 matches/inliers, 81.91% purity and 60%+ coverage;
# the strongest known wrong-stamp control reaches only 41/13 after the same
# trim.  Keep every boundary independent instead of relaxing existing gates.
TRIMMED_CHROMATIC_QUANTILE = 0.02
TRIMMED_CHROMATIC_MIN_GOOD_MATCHES = 90
TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS = 75
TRIMMED_CHROMATIC_MIN_INLIER_RATIO = 0.80
TRIMMED_CHROMATIC_MIN_SURFACE_COVERAGE = 0.50

# A numbered Samsung service-center pickup stamp has two independent semantic
# anchors before geometry is considered: OCR must literally retain
# ``三星电子服务中心`` from the current seal, and the printed requirement must
# be the exact ``取机专用章 + seven-digit station`` structure shared with the
# human-confirmed reference.  This allows a heavily table-occluded repeat to
# sit slightly below the generic 80-inlier single-reference boundary, while
# still requiring broad, pure geometry.  The complete 301-image reference
# matrix has one positive at 99/74 matches/inliers, 74.75% purity and
# 59.35%/51.58% coverage; the known wrong-stamp control is 46/24 with only
# 8.6% reference coverage.
BRANDED_STATION_MIN_GOOD_MATCHES = 95
BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS = 70
BRANDED_STATION_MIN_INLIER_RATIO = 0.72
BRANDED_STATION_MIN_SURFACE_COVERAGE = 0.50

# A complete but conflicting OCR company name remains a hard block for every
# generic visual-reference route.  One exceptionally strong, independently
# constrained fallback is allowed for a repeated 售后专用章: the current OCR
# must still contain the exact specific stamp type and a company fragment,
# while one human-confirmed same-requirement reference must form very dense
# geometry in both the regular and chromatic-crop representations. Across two
# real reruns the target regular geometry was 192/136 or 198/156; its
# independent chromatic geometry was 180/145 at 80.56% purity and 55%+
# bidirectional coverage. The strongest known wrong-stamp control is only
# 46/24 regular and 46/21 chromatic. All thresholds are independent AND gates;
# neither representation may promote the result alone.
COMPANY_CONFLICT_REFERENCE_MIN_GOOD_MATCHES = 180
COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS = 130
COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO = 0.70
COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE = 0.50
COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES = 175
COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS = 140
COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO = 0.78
COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE = 0.50
COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE = 0.82
COMPANY_CONFLICT_REFERENCE_MIN_SHARED_FRAGMENT = 6

# A 收货专用章 can be obscured by the signature/table intersection so that
# OCR reads one wrong company glyph while independently retaining the exact
# stamp type.  This route is deliberately narrower than the generic company
# conflict fallback: the observed legal company must have exactly one
# same-position glyph substitution, the stamp type must be independently
# present, and regular/chromatic SIFT must both cover most of the same
# human-confirmed reference.  In the complete 301-image matrix the sole true
# candidate is 130/106 regular and 133/102 chromatic with 64%+ bidirectional
# coverage.  Known wrong-stamp controls peak at 68/56 and 63/51.
RECEIVING_ONE_GLYPH_MIN_COMPANY_SCORE = 0.87
RECEIVING_ONE_GLYPH_MIN_GOOD_MATCHES = 125
RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS = 100
RECEIVING_ONE_GLYPH_MIN_INLIER_RATIO = 0.80
RECEIVING_ONE_GLYPH_MIN_SURFACE_COVERAGE = 0.60
RECEIVING_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES = 125
RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS = 100
RECEIVING_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO = 0.75
RECEIVING_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE = 0.60

# A bare legal-company round seal has no independent stamp-type text anchor.
# Therefore one OCR glyph substitution may be recovered only when *three*
# visual representations agree with one same-requirement human-confirmed
# reference: regular SIFT, chromatic-crop SIFT, and the registered whole-ink
# color mask.  In the complete 301-image matrix the sole candidate is
# 99/65 regular, 94/61 chromatic, and 0.7856 color-mask score. The closest
# known wrong stamp is only 68/56, 63/51, and 0.4087 respectively.
BARE_COMPANY_ONE_GLYPH_MIN_COMPANY_SCORE = 0.83
BARE_COMPANY_ONE_GLYPH_MIN_GOOD_MATCHES = 95
BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS = 60
BARE_COMPANY_ONE_GLYPH_MIN_INLIER_RATIO = 0.64
BARE_COMPANY_ONE_GLYPH_MIN_SURFACE_COVERAGE = 0.60
BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES = 90
BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS = 60
BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO = 0.64
BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE = 0.58
BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE = 0.77
BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_CORRELATION = 0.78
BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_DICE = 0.77

# A bare legal-company requirement can be clipped at the beginning by a table
# line while retaining an exact long suffix (for example OCR reads
# ``齐贵迪电子有限公司`` for ``乌鲁木齐贵迪电子有限公司``). This is distinct
# from fuzzy company correction: the recognized company must be a literal
# suffix after losing only 1–4 leading characters, and both regular and
# chromatic SIFT representations must be exceptionally strong. The complete
# matrix has one true candidate at 203/166 regular and 189/166 chromatic
# matches/inliers; the strongest known wrong stamp is only 68/56 and 63/51.
CLIPPED_COMPANY_REFERENCE_MIN_RECOGNIZED_LENGTH = 8
CLIPPED_COMPANY_REFERENCE_MAX_MISSING_PREFIX = 4
CLIPPED_COMPANY_REFERENCE_MIN_COMPANY_SCORE = 0.60
CLIPPED_COMPANY_REFERENCE_MIN_GOOD_MATCHES = 195
CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS = 160
CLIPPED_COMPANY_REFERENCE_MIN_INLIER_RATIO = 0.80
CLIPPED_COMPANY_REFERENCE_MIN_CANDIDATE_COVERAGE = 0.38
CLIPPED_COMPANY_REFERENCE_MIN_REFERENCE_COVERAGE = 0.55
CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES = 180
CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS = 160
CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_INLIER_RATIO = 0.85
CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_CANDIDATE_COVERAGE = 0.50
CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_REFERENCE_COVERAGE = 0.60

# Color-ink geometry is a fallback for faded scans where SIFT cannot retain
# enough local keypoints.  The full 301-receipt matrix has a wide margin:
# accepted positives start at 0.8158/0.8096/0.8319 for composite score,
# correlation and Dice, while the strongest wrong-stamp control reaches only
# 0.4337/0.4535/0.3828.  Require all three 0.80 boundaries independently.
COLOR_MASK_MIN_SCORE = 0.80
COLOR_MASK_MIN_CORRELATION = 0.80
COLOR_MASK_MIN_DICE = 0.80
COLOR_MASK_CANVAS_SIZE = 320
COLOR_MASK_ROTATIONS = tuple(range(-12, 13, 2))

# A moderately faded whole-ink match may still be promoted when the *same*
# candidate/reference pair also has broad, high-purity SIFT geometry. This is
# a separate AND gate, not a relaxation of the strict color-only route.
COLOR_SIFT_MIN_SCORE = 0.70
COLOR_SIFT_MIN_CORRELATION = 0.70
COLOR_SIFT_MIN_DICE = 0.65
COLOR_SIFT_MIN_GOOD_MATCHES = 60
COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS = 40
COLOR_SIFT_MIN_INLIER_RATIO = 0.65
COLOR_SIFT_MIN_SURFACE_COVERAGE = 0.30

# Whole-ink registration remains useful when table lines destroy local SIFT
# purity.  A bare company seal may use this fallback only when the *same*
# detected region agrees with two different confirmed files.  The full
# 301-image matrix has one pending positive: its leading/second registered
# masks score 0.7559/0.6865, while the known wrong-stamp control reaches only
# 0.4337/0.3042.  Both votes must retain modest local-feature support as an
# independent guard against similarly shaped generic round seals.
COLOR_MASK_CONSENSUS_MIN_DISTINCT_REFERENCES = 2
COLOR_MASK_CONSENSUS_MIN_TOP_SCORE = 0.74
COLOR_MASK_CONSENSUS_MIN_SCORE = 0.68
COLOR_MASK_CONSENSUS_MIN_CORRELATION = 0.68
COLOR_MASK_CONSENSUS_MIN_DICE = 0.70
COLOR_MASK_CONSENSUS_MIN_GOOD_MATCHES = 50
COLOR_MASK_CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 15
COLOR_MASK_CONSENSUS_MIN_CANDIDATE_COVERAGE = 0.15
COLOR_MASK_CONSENSUS_MIN_REFERENCE_COVERAGE = 0.20
COLOR_MASK_CONSENSUS_MIN_COMPANY_SCORE = 0.15
COLOR_MASK_CONSENSUS_MIN_RECOGNIZED_CHARS = 2

# A non-legal-name organization seal may lose its trailing text to a table
# line while OCR still reads a long, exact requirement prefix.  Promote only
# when that independent semantic prefix and two distinct confirmed files all
# agree on the same detected region.  The 301-image audit isolates one pending
# positive (0.6791/0.6480); the strongest known wrong-stamp control is 0.4337.
PREFIX_COLOR_CONSENSUS_MIN_DISTINCT_REFERENCES = 2
PREFIX_COLOR_CONSENSUS_MIN_TOP_SCORE = 0.67
PREFIX_COLOR_CONSENSUS_MIN_SCORE = 0.64
PREFIX_COLOR_CONSENSUS_MIN_CORRELATION = 0.66
PREFIX_COLOR_CONSENSUS_MIN_DICE = 0.59
PREFIX_COLOR_CONSENSUS_MIN_GOOD_MATCHES = 28
PREFIX_COLOR_CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 15
PREFIX_COLOR_CONSENSUS_MIN_INLIER_RATIO = 0.53
PREFIX_COLOR_CONSENSUS_MIN_CANDIDATE_COVERAGE = 0.19
PREFIX_COLOR_CONSENSUS_MIN_REFERENCE_COVERAGE = 0.21
PREFIX_COLOR_CONSENSUS_MIN_COMPANY_SCORE = 0.70
PREFIX_COLOR_CONSENSUS_MIN_PREFIX_CHARS = 7


def _reference_thresholds() -> dict:
    return {
        "strict": {
            "good_matches": MIN_GOOD_MATCHES,
            "homography_inliers": MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": MIN_INLIER_RATIO,
            "surface_coverage": MIN_SURFACE_COVERAGE,
        },
        "high_ratio": {
            "good_matches": HIGH_RATIO_MIN_GOOD_MATCHES,
            "homography_inliers": HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": HIGH_RATIO_MIN_INLIER_RATIO,
            "surface_coverage": HIGH_RATIO_MIN_SURFACE_COVERAGE,
        },
        "high_support": {
            "good_matches": HIGH_SUPPORT_MIN_GOOD_MATCHES,
            "homography_inliers": HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": HIGH_SUPPORT_MIN_INLIER_RATIO,
            "surface_coverage": HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
        },
        "ultra_support": {
            "good_matches": ULTRA_SUPPORT_MIN_GOOD_MATCHES,
            "homography_inliers": ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": ULTRA_SUPPORT_MIN_INLIER_RATIO,
            "surface_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
        },
        "branded_station_single_reference": {
            "good_matches": BRANDED_STATION_MIN_GOOD_MATCHES,
            "homography_inliers": (
                BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": BRANDED_STATION_MIN_INLIER_RATIO,
            "surface_coverage": (
                BRANDED_STATION_MIN_SURFACE_COVERAGE
            ),
            "required_text": "三星电子服务中心",
            "requirement_structure": "取机专用章 + 7位站号",
        },
        "company_conflict_ultra_reference": {
            "good_matches": (
                COMPANY_CONFLICT_REFERENCE_MIN_GOOD_MATCHES
            ),
            "homography_inliers": (
                COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": (
                COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO
            ),
            "surface_coverage": (
                COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE
            ),
            "chromatic_good_matches": (
                COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES
            ),
            "chromatic_homography_inliers": (
                COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
            ),
            "chromatic_inlier_ratio": (
                COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO
            ),
            "chromatic_surface_coverage": (
                COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE
            ),
            "company_score": (
                COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE
            ),
            "shared_company_fragment": (
                COMPANY_CONFLICT_REFERENCE_MIN_SHARED_FRAGMENT
            ),
            "specific_stamp_type": "售后专用章",
        },
        "receiving_one_glyph_reference": {
            "company_score": RECEIVING_ONE_GLYPH_MIN_COMPANY_SCORE,
            "company_glyph_substitutions": 1,
            "specific_stamp_type": "收货专用章",
            "good_matches": RECEIVING_ONE_GLYPH_MIN_GOOD_MATCHES,
            "homography_inliers": (
                RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": RECEIVING_ONE_GLYPH_MIN_INLIER_RATIO,
            "surface_coverage": (
                RECEIVING_ONE_GLYPH_MIN_SURFACE_COVERAGE
            ),
            "chromatic_good_matches": (
                RECEIVING_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES
            ),
            "chromatic_homography_inliers": (
                RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
            ),
            "chromatic_inlier_ratio": (
                RECEIVING_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO
            ),
            "chromatic_surface_coverage": (
                RECEIVING_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE
            ),
        },
        "bare_company_one_glyph_reference": {
            "company_score": BARE_COMPANY_ONE_GLYPH_MIN_COMPANY_SCORE,
            "company_glyph_substitutions": 1,
            "requirement_structure": "纯法定公司名",
            "good_matches": BARE_COMPANY_ONE_GLYPH_MIN_GOOD_MATCHES,
            "homography_inliers": (
                BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": BARE_COMPANY_ONE_GLYPH_MIN_INLIER_RATIO,
            "surface_coverage": (
                BARE_COMPANY_ONE_GLYPH_MIN_SURFACE_COVERAGE
            ),
            "chromatic_good_matches": (
                BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES
            ),
            "chromatic_homography_inliers": (
                BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
            ),
            "chromatic_inlier_ratio": (
                BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO
            ),
            "chromatic_surface_coverage": (
                BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE
            ),
            "color_mask_score": BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE,
            "color_mask_correlation": (
                BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_CORRELATION
            ),
            "color_mask_dice": BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_DICE,
        },
        "clipped_prefix_company_ultra_reference": {
            "recognized_length": (
                CLIPPED_COMPANY_REFERENCE_MIN_RECOGNIZED_LENGTH
            ),
            "maximum_missing_prefix": (
                CLIPPED_COMPANY_REFERENCE_MAX_MISSING_PREFIX
            ),
            "company_score": CLIPPED_COMPANY_REFERENCE_MIN_COMPANY_SCORE,
            "good_matches": CLIPPED_COMPANY_REFERENCE_MIN_GOOD_MATCHES,
            "homography_inliers": (
                CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": CLIPPED_COMPANY_REFERENCE_MIN_INLIER_RATIO,
            "candidate_coverage": (
                CLIPPED_COMPANY_REFERENCE_MIN_CANDIDATE_COVERAGE
            ),
            "reference_coverage": (
                CLIPPED_COMPANY_REFERENCE_MIN_REFERENCE_COVERAGE
            ),
            "chromatic_good_matches": (
                CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES
            ),
            "chromatic_homography_inliers": (
                CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
            ),
            "chromatic_inlier_ratio": (
                CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_INLIER_RATIO
            ),
            "chromatic_candidate_coverage": (
                CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_CANDIDATE_COVERAGE
            ),
            "chromatic_reference_coverage": (
                CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_REFERENCE_COVERAGE
            ),
        },
        "consensus": {
            "distinct_references": CONSENSUS_MIN_DISTINCT_REFERENCES,
            "top_inliers": CONSENSUS_MIN_TOP_INLIERS,
            "good_matches": CONSENSUS_MIN_GOOD_MATCHES,
            "homography_inliers": CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": CONSENSUS_MIN_INLIER_RATIO,
            "surface_coverage": CONSENSUS_MIN_SURFACE_COVERAGE,
        },
        "high_purity_consensus": {
            "distinct_references": (
                HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES
            ),
            "top_inliers": HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS,
            "good_matches": HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES,
            "homography_inliers": (
                HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO,
            "surface_coverage": (
                HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE
            ),
        },
        "chromatic_crop_consensus": {
            "distinct_references": (
                CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES
            ),
            "good_matches": CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES,
            "homography_inliers": (
                CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": CHROMATIC_CONSENSUS_MIN_INLIER_RATIO,
            "surface_coverage": (
                CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE
            ),
            "top_inliers": CHROMATIC_CONSENSUS_MIN_TOP_INLIERS,
            "top_inlier_ratio": (
                CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO
            ),
        },
        "trimmed_chromatic_single_reference": {
            "trim_quantile": TRIMMED_CHROMATIC_QUANTILE,
            "good_matches": TRIMMED_CHROMATIC_MIN_GOOD_MATCHES,
            "homography_inliers": (
                TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": TRIMMED_CHROMATIC_MIN_INLIER_RATIO,
            "surface_coverage": (
                TRIMMED_CHROMATIC_MIN_SURFACE_COVERAGE
            ),
        },
        "color_mask": {
            "score": COLOR_MASK_MIN_SCORE,
            "correlation": COLOR_MASK_MIN_CORRELATION,
            "dice": COLOR_MASK_MIN_DICE,
        },
        "color_sift": {
            "score": COLOR_SIFT_MIN_SCORE,
            "correlation": COLOR_SIFT_MIN_CORRELATION,
            "dice": COLOR_SIFT_MIN_DICE,
            "good_matches": COLOR_SIFT_MIN_GOOD_MATCHES,
            "homography_inliers": COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": COLOR_SIFT_MIN_INLIER_RATIO,
            "surface_coverage": COLOR_SIFT_MIN_SURFACE_COVERAGE,
        },
        "color_mask_consensus": {
            "distinct_references": (
                COLOR_MASK_CONSENSUS_MIN_DISTINCT_REFERENCES
            ),
            "top_score": COLOR_MASK_CONSENSUS_MIN_TOP_SCORE,
            "score": COLOR_MASK_CONSENSUS_MIN_SCORE,
            "correlation": COLOR_MASK_CONSENSUS_MIN_CORRELATION,
            "dice": COLOR_MASK_CONSENSUS_MIN_DICE,
            "good_matches": COLOR_MASK_CONSENSUS_MIN_GOOD_MATCHES,
            "homography_inliers": (
                COLOR_MASK_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
            ),
            "candidate_coverage": (
                COLOR_MASK_CONSENSUS_MIN_CANDIDATE_COVERAGE
            ),
            "reference_coverage": (
                COLOR_MASK_CONSENSUS_MIN_REFERENCE_COVERAGE
            ),
            "company_score": COLOR_MASK_CONSENSUS_MIN_COMPANY_SCORE,
            "recognized_company_chars": (
                COLOR_MASK_CONSENSUS_MIN_RECOGNIZED_CHARS
            ),
        },
        "strong_prefix_color_mask_consensus": {
            "distinct_references": (
                PREFIX_COLOR_CONSENSUS_MIN_DISTINCT_REFERENCES
            ),
            "top_score": PREFIX_COLOR_CONSENSUS_MIN_TOP_SCORE,
            "score": PREFIX_COLOR_CONSENSUS_MIN_SCORE,
            "correlation": PREFIX_COLOR_CONSENSUS_MIN_CORRELATION,
            "dice": PREFIX_COLOR_CONSENSUS_MIN_DICE,
            "good_matches": PREFIX_COLOR_CONSENSUS_MIN_GOOD_MATCHES,
            "homography_inliers": (
                PREFIX_COLOR_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
            ),
            "inlier_ratio": PREFIX_COLOR_CONSENSUS_MIN_INLIER_RATIO,
            "candidate_coverage": (
                PREFIX_COLOR_CONSENSUS_MIN_CANDIDATE_COVERAGE
            ),
            "reference_coverage": (
                PREFIX_COLOR_CONSENSUS_MIN_REFERENCE_COVERAGE
            ),
            "company_score": PREFIX_COLOR_CONSENSUS_MIN_COMPANY_SCORE,
            "recognized_prefix_chars": (
                PREFIX_COLOR_CONSENSUS_MIN_PREFIX_CHARS
            ),
            "requirement_structure": "非纯法定公司名的连续文字要求",
        },
    }
