from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.seal_reference import (
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
    CONSENSUS_MIN_DISTINCT_REFERENCES,
    CONSENSUS_MIN_GOOD_MATCHES,
    CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CONSENSUS_MIN_INLIER_RATIO,
    CONSENSUS_MIN_SURFACE_COVERAGE,
    CONSENSUS_MIN_TOP_INLIERS,
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
    SealReference,
    SealReferenceMatcher,
    ULTRA_SUPPORT_MIN_GOOD_MATCHES,
    ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    ULTRA_SUPPORT_MIN_INLIER_RATIO,
    ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
    _best_consensus,
    _passes_high_ratio_gate,
    _passes_high_support_gate,
    _passes_ultra_support_gate,
    _passes_color_mask_gate,
    _passes_color_sift_gate,
)


def _result(url: str, *, conflict: bool = False) -> dict:
    return {
        "filename": "candidate.jpg",
        "seal_check": {
            "requirement": "测试科技有限公司维修专用章",
            "recognized": "测试科技有限公司",
            "score": 0.76,
            "status": "无法判断",
            "reliable": False,
            "company_conflict": conflict,
        },
        "processing_artifacts": {
            "seals": [{"index": 0, "color_isolated_url": url}]
        },
    }


def test_reference_match_requires_every_strict_boundary(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    candidate = artifact_root / "candidate" / "seal.png"
    reference = artifact_root / "reference" / "seal.png"
    candidate.parent.mkdir(parents=True)
    reference.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    reference.write_bytes(b"reference")
    matcher = SealReferenceMatcher(artifact_root)
    requirement = "测试科技有限公司维修专用章"
    matcher.references = {
        requirement: [SealReference(
            filename="confirmed.jpg",
            requirement=requirement,
            artifact_url="/files/artifacts/reference/seal.png",
            path=reference,
        )]
    }
    metrics = {
        "good_matches": MIN_GOOD_MATCHES,
        "homography_inliers": MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": MIN_INLIER_RATIO,
        "candidate_coverage": MIN_SURFACE_COVERAGE,
        "reference_coverage": MIN_SURFACE_COVERAGE,
    }
    monkeypatch.setattr(matcher, "_compare", lambda *_a: dict(metrics))
    result = _result("/files/artifacts/candidate/seal.png")

    accepted = matcher.match(result)
    assert accepted["accepted"] is True
    assert accepted["reference_filename"] == "confirmed.jpg"

    for key in metrics:
        reduced = dict(metrics)
        reduced[key] = metrics[key] - (1 if key in {
            "good_matches", "homography_inliers"
        } else 0.001)
        monkeypatch.setattr(
            matcher, "_compare", lambda *_a, value=reduced: dict(value)
        )
        assert matcher.match(result)["accepted"] is False


def test_reference_match_rejects_company_conflict_before_visual_comparison(
    tmp_path, monkeypatch
):
    matcher = SealReferenceMatcher(tmp_path)
    monkeypatch.setattr(
        matcher,
        "_compare",
        lambda *_a: (_ for _ in ()).throw(AssertionError("不应执行视觉匹配")),
    )
    evidence = matcher.match(_result(
        "/files/artifacts/candidate/seal.png", conflict=True
    ))
    assert evidence["accepted"] is False
    assert "前提" in evidence["reason"]


def _consensus_evidence(
    reference: str,
    *,
    candidate_index: int = 0,
    inliers: int = CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
) -> dict:
    return {
        "candidate_index": candidate_index,
        "candidate_url": f"/files/artifacts/candidate/{candidate_index}.png",
        "reference_filename": reference,
        "reference_url": f"/files/artifacts/reference/{reference}.png",
        "good_matches": CONSENSUS_MIN_GOOD_MATCHES,
        "homography_inliers": inliers,
        "inlier_ratio": CONSENSUS_MIN_INLIER_RATIO,
        "candidate_coverage": CONSENSUS_MIN_SURFACE_COVERAGE,
        "reference_coverage": CONSENSUS_MIN_SURFACE_COVERAGE,
    }


def test_multi_reference_consensus_requires_two_distinct_files_on_same_region():
    evidence = [
        _consensus_evidence(
            "confirmed-a.jpg", inliers=CONSENSUS_MIN_TOP_INLIERS
        ),
        _consensus_evidence("confirmed-b.jpg"),
    ]
    accepted = _best_consensus(evidence)
    assert CONSENSUS_MIN_DISTINCT_REFERENCES == 2
    assert accepted["accepted"] is True
    assert accepted["candidate_index"] == 0
    assert accepted["reference_count"] == 2

    duplicate_file = [
        evidence[0],
        {**evidence[1], "reference_filename": "confirmed-a.jpg"},
    ]
    assert _best_consensus(duplicate_file)["accepted"] is False

    split_regions = [
        evidence[0],
        {**evidence[1], "candidate_index": 1},
    ]
    assert _best_consensus(split_regions)["accepted"] is False


def test_multi_reference_consensus_enforces_every_vote_boundary():
    first = _consensus_evidence(
        "confirmed-a.jpg", inliers=CONSENSUS_MIN_TOP_INLIERS
    )
    second = _consensus_evidence("confirmed-b.jpg")
    for key in (
        "good_matches", "homography_inliers", "inlier_ratio",
        "candidate_coverage", "reference_coverage",
    ):
        reduced = dict(second)
        reduced[key] -= 1 if key in {
            "good_matches", "homography_inliers"
        } else 0.001
        assert _best_consensus([first, reduced])["accepted"] is False

    weak_top = [
        {**first, "homography_inliers": CONSENSUS_MIN_TOP_INLIERS - 1},
        second,
    ]
    assert _best_consensus(weak_top)["accepted"] is False


def test_high_ratio_single_reference_enforces_compensating_boundaries():
    evidence = {
        "good_matches": HIGH_RATIO_MIN_GOOD_MATCHES,
        "homography_inliers": HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": HIGH_RATIO_MIN_INLIER_RATIO,
        "candidate_coverage": HIGH_RATIO_MIN_SURFACE_COVERAGE,
        "reference_coverage": HIGH_RATIO_MIN_SURFACE_COVERAGE,
    }
    assert _passes_high_ratio_gate(evidence) is True
    for key in evidence:
        reduced = dict(evidence)
        reduced[key] -= 1 if key in {
            "good_matches", "homography_inliers"
        } else 0.001
        assert _passes_high_ratio_gate(reduced) is False


def test_high_support_minor_coverage_enforces_every_compensating_boundary():
    evidence = {
        "good_matches": HIGH_SUPPORT_MIN_GOOD_MATCHES,
        "homography_inliers": HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": HIGH_SUPPORT_MIN_INLIER_RATIO,
        "candidate_coverage": HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
        "reference_coverage": HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
    }
    assert _passes_high_support_gate(evidence) is True
    for key in evidence:
        reduced = dict(evidence)
        reduced[key] -= 1 if key in {
            "good_matches", "homography_inliers"
        } else 0.001
        assert _passes_high_support_gate(reduced) is False


def test_ultra_support_partial_coverage_enforces_every_boundary():
    evidence = {
        "good_matches": ULTRA_SUPPORT_MIN_GOOD_MATCHES,
        "homography_inliers": ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": ULTRA_SUPPORT_MIN_INLIER_RATIO,
        "candidate_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
        "reference_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
    }
    assert _passes_ultra_support_gate(evidence) is True
    for key in evidence:
        reduced = dict(evidence)
        reduced[key] -= 1 if key in {
            "good_matches", "homography_inliers"
        } else 0.001
        assert _passes_ultra_support_gate(reduced) is False


def test_match_reports_ultra_support_partial_coverage_route(
    tmp_path, monkeypatch
):
    artifact_root = tmp_path / "artifacts"
    candidate = artifact_root / "candidate" / "seal.png"
    reference = artifact_root / "reference" / "seal.png"
    for path in (candidate, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    requirement = "测试科技有限公司维修专用章"
    matcher = SealReferenceMatcher(artifact_root)
    matcher.references = {requirement: [SealReference(
        filename="confirmed.jpg", requirement=requirement,
        artifact_url="/files/artifacts/reference/seal.png", path=reference,
    )]}
    monkeypatch.setattr(matcher, "_compare", lambda *_a: {
        "good_matches": ULTRA_SUPPORT_MIN_GOOD_MATCHES,
        "homography_inliers": ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": ULTRA_SUPPORT_MIN_INLIER_RATIO,
        "candidate_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
        "reference_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
    })
    monkeypatch.setattr(matcher, "_compare_color_mask", lambda *_a: {
        "color_mask_score": 0.0,
        "color_mask_correlation": 0.0,
        "color_mask_dice": 0.0,
        "color_mask_angle": 0,
        "color_mask_dx": 0,
        "color_mask_dy": 0,
    })
    evidence = matcher.match(_result(
        "/files/artifacts/candidate/seal.png"
    ))
    assert evidence["accepted"] is True
    assert evidence["route"] == "ultra_support_partial_coverage"
    assert evidence["confidence"] >= 0.92


def test_color_mask_geometry_enforces_score_correlation_and_dice_boundaries():
    evidence = {
        "color_mask_score": COLOR_MASK_MIN_SCORE,
        "color_mask_correlation": COLOR_MASK_MIN_CORRELATION,
        "color_mask_dice": COLOR_MASK_MIN_DICE,
    }
    assert _passes_color_mask_gate(evidence) is True
    for key in evidence:
        reduced = dict(evidence)
        reduced[key] -= 0.001
        assert _passes_color_mask_gate(reduced) is False


def test_color_sift_geometry_requires_all_whole_and_local_boundaries():
    evidence = {
        "color_mask_score": COLOR_SIFT_MIN_SCORE,
        "color_mask_correlation": COLOR_SIFT_MIN_CORRELATION,
        "color_mask_dice": COLOR_SIFT_MIN_DICE,
        "good_matches": COLOR_SIFT_MIN_GOOD_MATCHES,
        "homography_inliers": COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": COLOR_SIFT_MIN_INLIER_RATIO,
        "candidate_coverage": COLOR_SIFT_MIN_SURFACE_COVERAGE,
        "reference_coverage": COLOR_SIFT_MIN_SURFACE_COVERAGE,
    }
    assert _passes_color_sift_gate(evidence) is True
    for key in evidence:
        reduced = dict(evidence)
        reduced[key] -= 1 if key in {
            "good_matches", "homography_inliers"
        } else 0.001
        assert _passes_color_sift_gate(reduced) is False


def test_match_reports_color_sift_route_for_same_pair_joint_evidence(
    tmp_path, monkeypatch
):
    artifact_root = tmp_path / "artifacts"
    candidate = artifact_root / "candidate" / "seal.png"
    reference = artifact_root / "reference" / "seal.png"
    for path in (candidate, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    requirement = "测试科技有限公司维修专用章"
    matcher = SealReferenceMatcher(artifact_root)
    matcher.references = {requirement: [SealReference(
        filename="confirmed.jpg", requirement=requirement,
        artifact_url="/files/artifacts/reference/seal.png", path=reference,
    )]}
    monkeypatch.setattr(matcher, "_compare", lambda *_a: {
        "good_matches": COLOR_SIFT_MIN_GOOD_MATCHES,
        "homography_inliers": COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
        "inlier_ratio": COLOR_SIFT_MIN_INLIER_RATIO,
        "candidate_coverage": COLOR_SIFT_MIN_SURFACE_COVERAGE,
        "reference_coverage": COLOR_SIFT_MIN_SURFACE_COVERAGE,
    })
    monkeypatch.setattr(matcher, "_compare_color_mask", lambda *_a: {
        "color_mask_score": COLOR_SIFT_MIN_SCORE,
        "color_mask_correlation": COLOR_SIFT_MIN_CORRELATION,
        "color_mask_dice": COLOR_SIFT_MIN_DICE,
        "color_mask_angle": 0, "color_mask_dx": 0, "color_mask_dy": 0,
    })
    evidence = matcher.match(_result("/files/artifacts/candidate/seal.png"))
    assert evidence["accepted"] is True
    assert evidence["route"] == "color_mask_sift_geometry"
    assert evidence["reference_filename"] == "confirmed.jpg"
    assert evidence["confidence"] >= 0.91


def test_match_reports_color_mask_geometry_when_sparse_sift_is_weak(
    tmp_path, monkeypatch
):
    artifact_root = tmp_path / "artifacts"
    candidate = artifact_root / "candidate" / "seal.png"
    reference = artifact_root / "reference" / "seal.png"
    for path in (candidate, reference):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    requirement = "测试科技有限公司维修专用章"
    matcher = SealReferenceMatcher(artifact_root)
    matcher.references = {requirement: [SealReference(
        filename="confirmed.jpg",
        requirement=requirement,
        artifact_url="/files/artifacts/reference/seal.png",
        path=reference,
    )]}
    monkeypatch.setattr(matcher, "_compare", lambda *_a: {
        "good_matches": 20,
        "homography_inliers": 10,
        "inlier_ratio": 0.5,
        "candidate_coverage": 0.2,
        "reference_coverage": 0.2,
    })
    monkeypatch.setattr(matcher, "_compare_color_mask", lambda *_a: {
        "color_mask_score": COLOR_MASK_MIN_SCORE,
        "color_mask_correlation": COLOR_MASK_MIN_CORRELATION,
        "color_mask_dice": COLOR_MASK_MIN_DICE,
        "color_mask_angle": 2,
        "color_mask_dx": 1,
        "color_mask_dy": -1,
    })
    evidence = matcher.match(_result("/files/artifacts/candidate/seal.png"))
    assert evidence["accepted"] is True
    assert evidence["route"] == "color_mask_geometry"
    assert evidence["reference_filename"] == "confirmed.jpg"
    assert evidence["confidence"] >= 0.91


def test_match_reports_multi_reference_route_and_leading_reference(
    tmp_path, monkeypatch
):
    artifact_root = tmp_path / "artifacts"
    candidate = artifact_root / "candidate" / "seal.png"
    reference_a = artifact_root / "reference" / "a.png"
    reference_b = artifact_root / "reference" / "b.png"
    for path in (candidate, reference_a, reference_b):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    requirement = "测试科技有限公司维修专用章"
    matcher = SealReferenceMatcher(artifact_root)
    matcher.references = {requirement: [
        SealReference(
            filename="confirmed-a.jpg", requirement=requirement,
            artifact_url="/files/artifacts/reference/a.png", path=reference_a,
        ),
        SealReference(
            filename="confirmed-b.jpg", requirement=requirement,
            artifact_url="/files/artifacts/reference/b.png", path=reference_b,
        ),
    ]}

    def compare(_candidate, reference):
        return {
            **_consensus_evidence(
                "unused",
                inliers=(
                    CONSENSUS_MIN_TOP_INLIERS
                    if reference.name == "a.png"
                    else CONSENSUS_MIN_HOMOGRAPHY_INLIERS
                ),
            ),
        } | {
            key: value for key, value in _consensus_evidence("unused").items()
            if key in {
                "good_matches", "inlier_ratio", "candidate_coverage",
                "reference_coverage",
            }
        }

    monkeypatch.setattr(matcher, "_compare", compare)
    evidence = matcher.match(
        _result("/files/artifacts/candidate/seal.png")
    )
    assert evidence["accepted"] is True
    assert evidence["route"] == "multi_reference_consensus"
    assert evidence["reference_filename"] == "confirmed-a.jpg"
    assert evidence["consensus_candidate_index"] == 0
    assert evidence["consensus_reference_count"] == 2


def test_sift_homography_covers_a_complete_repeated_stamp(tmp_path):
    matcher = SealReferenceMatcher(tmp_path)
    if not matcher.enabled:
        return
    canvas = np.full((800, 800), 255, dtype=np.uint8)
    cv2.circle(canvas, (400, 400), 310, 30, 12)
    cv2.circle(canvas, (400, 400), 235, 80, 5)
    cv2.putText(
        canvas, "CONFIRMED CUSTOMER STAMP", (95, 225),
        cv2.FONT_HERSHEY_SIMPLEX, 1.1, 20, 4, cv2.LINE_AA,
    )
    cv2.putText(
        canvas, "SERVICE 042", (235, 610),
        cv2.FONT_HERSHEY_SIMPLEX, 1.3, 20, 5, cv2.LINE_AA,
    )
    random = np.random.default_rng(20260814)
    for x, y in random.integers(120, 680, size=(180, 2)):
        cv2.circle(canvas, (int(x), int(y)), 2, 50, -1)
    reference = tmp_path / "reference.png"
    candidate = tmp_path / "candidate.png"
    cv2.imwrite(str(reference), canvas)
    matrix = cv2.getRotationMatrix2D((400, 400), 2.5, 0.98)
    transformed = cv2.warpAffine(
        canvas, matrix, (800, 800), borderValue=255
    )
    cv2.line(transformed, (0, 390), (799, 390), 255, 8)
    cv2.imwrite(str(candidate), transformed)

    metrics = matcher._compare(candidate, reference)
    assert metrics["good_matches"] >= MIN_GOOD_MATCHES
    assert metrics["homography_inliers"] >= MIN_HOMOGRAPHY_INLIERS
    assert metrics["inlier_ratio"] >= MIN_INLIER_RATIO
    assert metrics["candidate_coverage"] >= MIN_SURFACE_COVERAGE
    assert metrics["reference_coverage"] >= MIN_SURFACE_COVERAGE


def test_app_visual_reference_promotion_preserves_raw_ocr(monkeypatch):
    import app as app_module

    evidence = {
        "accepted": True,
        "candidate_index": 0,
        "candidate_url": "/files/artifacts/new/seal.png",
        "reference_filename": "confirmed.jpg",
        "reference_url": "/files/artifacts/ref/seal.png",
        "good_matches": 150,
        "homography_inliers": 120,
        "inlier_ratio": 0.8,
        "candidate_coverage": 0.45,
        "reference_coverage": 0.50,
        "confidence": 0.95,
    }
    monkeypatch.setattr(
        app_module.seal_reference_matcher, "match", lambda _result: evidence
    )
    result = {
        "filename": "candidate.jpg",
        "date_check": {
            "actual": "2025-01-05", "status": "匹配", "reliable": True
        },
        "seal_check": {
            "requirement": "测试科技有限公司维修专用章",
            "recognized": "测试科技有限公司",
            "status": "无法判断",
            "score": 0.76,
            "reliable": False,
            "backend": "本地 OCR",
        },
        "review_reasons": ["印章内容无法可靠判断"],
        "processing_artifacts": {
            "seals": [{"index": 0, "color_isolated_url": evidence["candidate_url"]}]
        },
        "overall": "需人工复核",
        "final_result": "需人工复核",
        "review_status": "待复核",
    }

    promoted = app_module._apply_visual_seal_reference(result)
    assert promoted["seal_check"]["recognized"] == "测试科技有限公司"
    assert promoted["seal_check"]["ocr_only_status"] == "无法判断"
    assert promoted["seal_check"]["status"] == "匹配"
    assert promoted["seal_check"]["reliable"] is True
    assert promoted["overall"] == "通过"
    assert promoted["review_status"] == "无需复核"
    assert promoted["processing_artifacts"]["seals"][0][
        "visual_reference_match"
    ]["reference_filename"] == "confirmed.jpg"
