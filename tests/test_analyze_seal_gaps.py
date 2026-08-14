import json
import sqlite3

from tools.analyze_seal_gaps import build_report


def test_seal_gap_report_uses_latest_immutable_machine_result(tmp_path):
    database = tmp_path / "results.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE results (id INTEGER PRIMARY KEY, filename TEXT, original_result_json TEXT)"
    )
    base = {
        "ocr_backend": "hybrid",
        "seal_check": {
            "requirement": "测试科技有限公司业务专用章",
            "recognized": "测试科技有限公司",
            "status": "无法判断",
            "score": 0.96,
            "company_score": 1.0,
            "company_conflict": False,
            "reliable": False,
        },
        "processing_artifacts": {
            "seals": [{
                "shape": "圆形",
                "original_url": "/files/original.jpg",
                "color_isolated_url": "/files/color.png",
            }]
        },
    }
    connection.execute(
        "INSERT INTO results VALUES (1, 'sample.jpg', ?)",
        (json.dumps({**base, "seal_check": {**base["seal_check"], "score": 0.2}}),),
    )
    connection.execute(
        "INSERT INTO results VALUES (2, 'sample.jpg', ?)",
        (json.dumps(base),),
    )
    connection.commit()
    connection.close()
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(
        json.dumps({"sample.jpg": {"seal_should_match": True}}),
        encoding="utf-8",
    )

    report = build_report(database, truth_path, "hybrid")

    assert report["summary"]["unreliable"] == 1
    assert report["summary"]["公司名较强但章类型或结构不足"] == 1
    assert report["summary"]["未执行 Server 章色复核"] == 1
    assert report["samples"][0]["result_id"] == 2
    assert report["samples"][0]["artifact_urls"] == [
        "/files/original.jpg",
        "/files/color.png",
    ]
