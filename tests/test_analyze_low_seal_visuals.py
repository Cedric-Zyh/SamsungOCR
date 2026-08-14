from pathlib import Path

import cv2
import numpy as np

from tools.analyze_low_seal_visuals import (
    analyze_report,
    color_band,
    resolve_artifact_url,
)


def test_color_band_boundaries():
    assert color_band(0.0299) == "章色极弱"
    assert color_band(0.03) == "章色偏弱"
    assert color_band(0.0999) == "章色偏弱"
    assert color_band(0.10) == "章色充足"


def test_low_seal_visual_report_prioritizes_colored_structured_stamp(tmp_path: Path):
    artifact_root = tmp_path / "artifacts"
    artifact_dir = artifact_root / "token" / "seals"
    artifact_dir.mkdir(parents=True)
    image = np.full((160, 240, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (25, 25), (215, 135), (30, 30, 220), 16)
    cv2.putText(
        image,
        "STAMP",
        (45, 95),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (30, 30, 220),
        5,
    )
    path = artifact_dir / "seal-0-original.jpg"
    assert cv2.imwrite(str(path), image)
    url = "/files/artifacts/token/seals/seal-0-original.jpg"
    report = {
        "backend": "hybrid",
        "samples": [{
            "filename": "sample.jpg",
            "result_id": 1,
            "truth_should_match": True,
            "category": "低相似度证据",
            "requirement": "示例有限公司维修专用章1234567",
            "recognized": "维修专用章12345",
            "score": 0.4,
            "company_score": 0.2,
            "shapes": ["矩形"],
            "server_audited": False,
            "artifact_urls": [url],
        }],
    }
    result = analyze_report(report, artifact_root)
    assert result["low_similarity_samples"] == 1
    assert result["missing_original_images"] == 0
    assert result["server_priority_samples"] == 1
    assert result["samples"][0]["color_band"] == "章色充足"
    assert result["samples"][0]["evidence_subcategory"] == "章型已识别但主体缺失"
    assert resolve_artifact_url(url, artifact_root) == path


def test_non_artifact_url_is_not_resolved(tmp_path: Path):
    assert resolve_artifact_url("/other/seal.jpg", tmp_path) is None
