from receipt_ocr.recognition.seal.postprocess.ring_text import reorder_ring_text


def test_reorders_company_text_cut_at_circular_seam():
    result = reorder_ring_text(
        "公司济南新宇航科技发展有限",
        "济南新宇航科技发展有限公司",
    )
    assert result["raw_text"] == "公司济南新宇航科技发展有限"
    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["changed"] is True
    assert result["seam_offset"] == 2
    assert result["reason"] == "company_suffix_cyclic_reorder"


def test_keeps_complete_ring_text_and_does_not_invent_requirement_characters():
    result = reorder_ring_text(
        "济南新宇航科技发展有限公司",
        "北京另一家公司有限公司",
    )
    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["changed"] is False


def test_can_use_alternative_observation_as_an_additional_ring_candidate():
    result = reorder_ring_text(
        "公司济南新宇航科技发展有限",
        "济南新宇航科技发展有限公司",
        alternatives=["济南新宇航科技发展有限公司"],
    )
    assert result["text"] == "济南新宇航科技发展有限公司"
    assert result["source"] in {
        "公司济南新宇航科技发展有限",
        "济南新宇航科技发展有限公司",
    }
