"""A local pass inside QingTong's box is one more channel, not a guess.

``seal_provider_policy`` refuses to let local fuzzy/geometry readings arbitrate
a seal verdict.  These tests pin the one case where that reasoning does not
apply -- the local pass re-read the exact box the API judged -- and the two
cases where it still does: an unmarked local reading, and ``all`` mode.
"""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr import stage_seal
from receipt_ocr.document_context import DocumentContext, StageRequest
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.seal_local_channel import (
    LOCAL_CHANNEL_SOURCE,
    local_seal_full_match,
    record_local_channel,
)

REQUIRED = "广州花东仓收货专用章"


def _write_image(path, width=400, height=600):
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (100, 200), (300, 400), (0, 0, 220), 6)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    encoded.tofile(str(path))
    return path


def _external(seals, ok=True):
    return {"ok": ok, "enabled": True, "response": {"data": {"img_0": seals}}}


def _customer_seal(xyxy=(100, 200, 300, 400), text=REQUIRED):
    return {
        "xyxy": list(xyxy),
        "matched_seal": {"label": text, "similarity": 0.93},
        "text_formatted": text,
        "text_similarity": 0.9,
    }


def _checked(**overrides):
    check = {
        "requirement": REQUIRED,
        "recognized": REQUIRED,
        "status": "匹配",
        "reliable": True,
        "score": 1.0,
        "confidence": 0.95,
    }
    check.update(overrides)
    return check


class _SealApi:
    def __init__(self, external):
        self._external = external
        self.calls = 0

    def recognize(self, source):
        self.calls += 1
        return self._external

    def skipped(self):
        return {"enabled": False, "requested": False, "message": "未请求印章接口"}


def _context(source):
    context = DocumentContext(source)
    context._pages = {
        "vision": [
            TextObservation(
                text="签章要求：收货专用章", confidence=0.99,
                x=0.1, y=0.471, width=0.3, height=0.02,
            )
        ]
    }
    return context


def _request(tmp_path, seal_mode, future=None):
    return StageRequest(
        dict(page="vision", date="vision", seal="vision"),
        {"签章要求": REQUIRED},
        tmp_path / "artifacts",
        "/files/artifacts/token",
        seal_mode,
        future,
    )


def _forbidden(name):
    def fail(*args, **kwargs):
        raise AssertionError(f"{name} should not be called")

    return fail


# --- the channel rule itself -------------------------------------------------

@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"region_source": "清瞳印章区域"}, True),
        ({"region_source": "本地检测"}, False),
        ({"region_source": None}, False),
        ({"region_source": "清瞳印章区域", "status": "部分匹配"}, False),
        ({"region_source": "清瞳印章区域", "reliable": False}, False),
        ({"region_source": "清瞳印章区域", "simulated": True}, False),
    ],
)
def test_only_a_boxed_exact_match_qualifies(overrides, expected):
    assert local_seal_full_match(_checked(**overrides)) is expected


def test_an_unmarked_local_reading_is_recorded_but_never_decides():
    verdict = {"status": "不匹配", "reliable": True}
    merged = record_local_channel(verdict, _checked(), "any")
    assert merged["status"] == "不匹配"
    assert merged["local_channel"]["recognized"] == REQUIRED


def test_a_boxed_exact_match_wins_over_a_provider_mismatch():
    verdict = {
        "status": "不匹配", "reliable": True,
        "recognized": "广州花东仓020DCH年月收货专用章",
        "all_recognized": ["广州花东仓020DCH年月收货专用章"],
        "source": "清瞳 · 印章文字 OCR",
    }
    merged = record_local_channel(
        verdict, _checked(region_source="清瞳印章区域"), "any"
    )
    assert merged["status"] == "匹配"
    assert merged["reliable"] is True
    assert merged["source"] == LOCAL_CHANNEL_SOURCE
    assert merged["recognized"] == REQUIRED
    # Both readings stay visible to the reviewer.
    assert "广州花东仓020DCH年月收货专用章" in merged["all_recognized"]
    assert REQUIRED in merged["all_recognized"]
    assert merged["local_channel"]["status"] == "匹配"


def test_all_mode_records_the_local_reading_without_letting_it_decide():
    merged = record_local_channel(
        {"status": "不匹配", "reliable": True},
        _checked(region_source="清瞳印章区域"),
        "all",
    )
    assert merged["status"] == "不匹配"
    assert "不参与判定" in merged["local_channel_note"]


def test_a_provider_match_keeps_its_own_reading():
    merged = record_local_channel(
        _checked(source="清瞳 · 印章模板识别"),
        _checked(region_source="清瞳印章区域", recognized="另一段文字"),
        "any",
    )
    assert merged["source"] == "清瞳 · 印章模板识别"
    assert merged["recognized"] == REQUIRED


# --- wiring: the local run reads QingTong's box ------------------------------

def test_local_run_reuses_the_prefetched_box_instead_of_detecting(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_customer_seal()]))
    future = SimpleNamespace(result=lambda: _external([_customer_seal()]))
    monkeypatch.setattr(stage_seal, "detect_seal_regions", _forbidden("detect_seal_regions"))
    captured = {}

    def fake_recognize(source_arg, rows, regions, *args, **kwargs):
        captured["regions"] = [region.to_dict() for region in regions]
        return [REQUIRED], []

    result = stage_seal.execute(
        _context(source), _request(tmp_path, "local", future), fake_recognize, api
    )

    assert len(captured["regions"]) == 1
    assert (captured["regions"][0]["x"], captured["regions"][0]["y"]) == pytest.approx(
        (0.25, 200 / 600)
    )
    seal_check = result["seal_check"]
    assert seal_check["region_source"] == "清瞳印章区域"
    assert seal_check["status"] == "匹配"
    assert local_seal_full_match(seal_check)
    # The local run never issues its own API request.
    assert api.calls == 0


def test_local_run_falls_back_to_its_own_detection_when_the_api_failed(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([], ok=False))
    future = SimpleNamespace(result=lambda: _external([], ok=False))
    monkeypatch.setattr(
        stage_seal, "detect_seal_regions", lambda source: [_detected_region()]
    )
    seen = {}

    def fake_recognize(source_arg, rows, regions, *args, **kwargs):
        seen["roles"] = [region.role for region in regions]
        return [], []

    result = stage_seal.execute(
        _context(source), _request(tmp_path, "local", future), fake_recognize, api
    )

    assert seen["roles"] == ["收货客户章"]
    assert "region_source" not in result["seal_check"]
    assert local_seal_full_match(result["seal_check"]) is False


def test_a_broken_prefetch_does_not_break_the_local_run(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")

    def boom():
        raise RuntimeError("remote timeout")

    monkeypatch.setattr(
        stage_seal, "detect_seal_regions", lambda source: [_detected_region()]
    )
    future = SimpleNamespace(result=boom)
    result = stage_seal.execute(
        _context(source),
        _request(tmp_path, "local", future),
        lambda *a, **k: ([], []),
        _SealApi(None),
    )
    assert result["seal_check"]["status"] != "匹配"
    assert "region_source" not in result["seal_check"]


def test_dual_mode_attaches_the_boxed_local_reading_as_a_channel(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_customer_seal(text="广州花东仓020DCH年月收货专用章")]))
    monkeypatch.setattr(stage_seal, "detect_seal_regions", _forbidden("detect_seal_regions"))

    def fake_recognize(source_arg, rows, regions, *args, **kwargs):
        return [REQUIRED], []

    result = stage_seal.execute(
        _context(source), _request(tmp_path, "qingtong"), fake_recognize, api
    )

    seal_check = result["seal_check"]
    assert seal_check["local_evidence"]["status"] == "匹配"
    assert seal_check["local_channel"]["status"] == "匹配"
    # The API read extra characters printed inside the stamp; the boxed local
    # pass read none, so the two channels disagree and the local one decides.
    assert seal_check["status"] == "匹配"
    assert seal_check["source"] == LOCAL_CHANNEL_SOURCE


def test_dual_mode_all_keeps_the_provider_verdict(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_customer_seal(text="广州花东仓020DCH年月收货专用章")]))
    monkeypatch.setattr(stage_seal, "detect_seal_regions", _forbidden("detect_seal_regions"))
    request = StageRequest(
        dict(page="vision", date="vision", seal="vision"),
        {"签章要求": REQUIRED},
        tmp_path / "artifacts",
        "/files/artifacts/token",
        "qingtong",
        None,
        {"seal_match_mode": "all"},
    )
    result = stage_seal.execute(
        _context(source), request, lambda *a, **k: ([REQUIRED], []), api
    )
    seal_check = result["seal_check"]
    assert seal_check["status"] == "不匹配"
    assert "不参与判定" in seal_check["local_channel_note"]


def _detected_region():
    from receipt_ocr.image_processing import SealRegion

    return SealRegion(
        x=0.2, y=0.3, width=0.3, height=0.2, color="red",
        role="收货客户章", pixel_ratio=0.4,
    )
