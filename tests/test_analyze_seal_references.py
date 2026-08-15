from tools.analyze_seal_references import build_safe_rerun_plan


def test_reference_rerun_plan_contains_only_current_positive_candidates():
    plan = build_safe_rerun_plan([
        {
            "filename": "b.jpg",
            "accepted": True,
            "truth_should_match": True,
            "source": "当前剩余样本矩阵",
        },
        {
            "filename": "a.jpg",
            "accepted": True,
            "truth_should_match": True,
            "source": "当前剩余样本矩阵",
        },
        {
            "filename": "stored.jpg",
            "accepted": True,
            "truth_should_match": True,
            "source": "已保存生产证据",
        },
        {
            "filename": "rejected.jpg",
            "accepted": False,
            "truth_should_match": True,
            "source": "当前剩余样本矩阵",
        },
    ])

    assert plan == {
        "safe": True,
        "candidate_count": 2,
        "filenames": ["a.jpg", "b.jpg"],
        "sample_arguments": [
            "--sample", "a.jpg", "--sample", "b.jpg"
        ],
        "blocked_by_false_accepts": [],
        "note": "已知负例全部拒绝，可对候选执行完整 OCR 重跑",
    }


def test_reference_rerun_plan_is_blocked_by_any_false_accept():
    plan = build_safe_rerun_plan([
        {
            "filename": "positive.jpg",
            "accepted": True,
            "truth_should_match": True,
            "source": "当前剩余样本矩阵",
        },
        {
            "filename": "negative.jpg",
            "accepted": True,
            "truth_should_match": False,
            "source": "当前剩余样本矩阵",
        },
    ])

    assert plan["safe"] is False
    assert plan["candidate_count"] == 0
    assert plan["filenames"] == []
    assert plan["sample_arguments"] == []
    assert plan["blocked_by_false_accepts"] == ["negative.jpg"]
