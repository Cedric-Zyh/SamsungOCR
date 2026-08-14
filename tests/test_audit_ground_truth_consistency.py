from tools.audit_ground_truth_consistency import field_mismatches


def _result(requirement: str, confidence: float = 0.998) -> dict:
    return {
        "fields": {"签章要求": requirement},
        "field_metadata": {
            "签章要求": {
                "confidence": confidence,
                "source": "OCR",
            },
        },
        "raw_text": f"签章要求：\n{requirement}",
        "seal_check": {"requirement": requirement},
    }


def test_truth_audit_flags_high_confidence_missing_stamp_identifier():
    mismatches = field_mismatches(
        _result("云南邮维科技有限公司检测专用章（3）"),
        {"签章要求": "云南邮维科技有限公司检测专用章"},
    )
    assert mismatches == [{
        "field": "签章要求",
        "expected": "云南邮维科技有限公司检测专用章",
        "recognized": "云南邮维科技有限公司检测专用章（3）",
        "confidence": 0.998,
        "source": "OCR",
        "raw_text_support": True,
        "seal_pipeline_support": True,
        "needs_truth_review": True,
    }]


def test_truth_audit_never_auto_flags_low_confidence_or_equal_values():
    low = field_mismatches(
        _result("云南邮维科技有限公司检测专用章（3）", 0.71),
        {"签章要求": "云南邮维科技有限公司检测专用章"},
    )
    assert low[0]["needs_truth_review"] is False
    assert field_mismatches(
        _result("云南邮维科技有限公司检测专用章（3）"),
        {"签章要求": "云南邮维科技有限公司检测专用章(3)"},
    ) == []
