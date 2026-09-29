from receipt_ocr.stages.seal import _ellipse_channels, _ellipse_display_texts


def test_round_stamp_combines_ring_and_horizontal_type_channels():
    artifacts = [{
        "shape": "圆形",
        "unwrapped_text": "大连北华通信设备有限公司",
        "round_type_band_text": "售后专用章",
        "color_isolated_text": "",
    }]

    ring, type_text, _, _ = _ellipse_channels([], artifacts)

    assert ring == ["大连北华通信设备有限公司"]
    assert type_text == ["售后专用章"]
    assert _ellipse_display_texts([], artifacts) == [
        "大连北华通信设备有限公司",
        "售后专用章",
    ]


def test_rectangular_stamp_does_not_use_round_center_merge():
    artifacts = [{
        "shape": "矩形",
        "unwrapped_text": "公司",
        "round_type_band_text": "专用章",
    }]
    ring, type_text, _, _ = _ellipse_channels([], artifacts)
    assert ring == []
    assert type_text == []
