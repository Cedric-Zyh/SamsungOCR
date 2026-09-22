"""The QingTong stamp box is the local region, so we stop guessing our own.

Two separate promises are covered here:

* ``qingtong_only`` runs no local OCR by contract, but the boxes the API reports
  must still land in ``seal_check['regions']`` and ``processing_artifacts`` --
  they used to be empty on every production record.
* ``qingtong`` keeps running local OCR, but on the API's box instead of a second
  detection around the footer.
"""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr import stage_seal
from receipt_ocr.document_context import DocumentContext, StageRequest
from receipt_ocr.image_processing import (
    read_image_size,
    save_pixel_region_crop,
)
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.qingtong_regions import (
    qingtong_region_boxes,
    qingtong_seal_regions,
)


def _write_image(path, width=400, height=600):
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (100, 200), (300, 400), (0, 0, 220), 6)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    encoded.tofile(str(path))
    return path


def _external(seals):
    return {
        "ok": True,
        "enabled": True,
        "response": {"data": {"img_0": seals}},
    }


def _customer_seal(xyxy=(100, 200, 300, 400)):
    return {
        "xyxy": list(xyxy),
        "matched_seal": {"label": "广州花东仓收货专用章", "similarity": 0.93},
        "text_formatted": "广州花东仓收货专用章",
        "text_similarity": 0.9,
    }


def _dispatch_seal(xyxy=(60, 120, 180, 240)):
    return {
        "xyxy": list(xyxy),
        "matched_seal": {"label": "商品付讫章"},
        "text_formatted": "商品付讫章",
    }


class _SealApi:
    def __init__(self, external):
        self._external = external
        self.calls = 0

    def recognize(self, source):
        self.calls += 1
        return self._external

    def skipped(self):
        return {"enabled": False, "requested": False, "message": "未请求印章接口"}


def _context(source, rows=None):
    context = DocumentContext(source)
    # A cached page keeps the seal stage off the OCR engine entirely; the rows
    # are only there to make the footer detectable when a case needs it.
    context._pages = {"vision": list(rows or [])}
    return context


def _footer_rows():
    return [
        TextObservation(
            text="签章要求：收货专用章", confidence=0.99,
            x=0.1, y=0.471, width=0.3, height=0.02,
        )
    ]


def _request(tmp_path, seal_mode, requirement="广州花东仓收货专用章"):
    return StageRequest(
        dict(page="vision", date="vision", seal="vision"),
        {"签章要求": requirement},
        tmp_path / "artifacts",
        "/files/artifacts/token",
        seal_mode,
    )


def test_pixel_box_round_trips_without_fraction_drift(tmp_path):
    source = _write_image(tmp_path / "page.jpg", width=401, height=601)
    assert read_image_size(source) == (401, 601)
    destination = tmp_path / "crop.png"
    save_pixel_region_crop(source, destination, (100, 200, 300, 400))
    assert destination.is_file()
    cropped = cv2.imdecode(np.fromfile(str(destination), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert cropped.shape[:2] == (200, 200)


def test_region_boxes_require_the_qingtong_policy():
    assert qingtong_region_boxes({}) == []
    assert qingtong_region_boxes({"dual_check": {"policy": "local", "candidates": []}}) == []
    rejected = {
        "dual_check": {
            "policy": "qingtong_any_channel",
            "candidates": [
                {"index": 0, "xyxy": [10, 20, 10, 40]},   # zero width
                {"index": 1, "xyxy": [10, 20, 30]},       # wrong length
                {"index": 2, "xyxy": [10, 20, 30, float("nan")]},
                {"index": 3, "xyxy": True},
                {"index": 4, "xyxy": [10, 20, 30, 40]},   # the only usable box
            ],
            "selected": {"index": 4},
        }
    }
    boxes = qingtong_region_boxes(rejected)
    assert [item["api_index"] for item in boxes] == [4]
    # The kept position starts at zero so the review page numbers one stamp "1".
    assert [item["index"] for item in boxes] == [0]
    assert boxes[0]["xyxy"] == (10.0, 20.0, 30.0, 40.0)
    assert boxes[0]["selected"] is True


def test_regions_express_the_box_as_page_fractions(tmp_path):
    source = _write_image(tmp_path / "page.jpg", width=400, height=600)
    check = {
        "dual_check": {
            "policy": "qingtong_any_channel",
            "candidates": [{"index": 0, "xyxy": [100, 200, 300, 400]}],
            "selected": {"index": 0},
        }
    }
    regions = qingtong_seal_regions(source, check)
    assert len(regions) == 1
    region = regions[0]
    assert (region.x, region.y) == pytest.approx((0.25, 200 / 600))
    assert (region.width, region.height) == pytest.approx((0.5, 200 / 600))
    assert region.role == "收货客户章"


def test_regions_are_empty_when_the_source_cannot_be_read():
    check = {
        "dual_check": {
            "policy": "qingtong_any_channel",
            "candidates": [{"index": 0, "xyxy": [1, 2, 3, 4]}],
            "selected": {"index": 0},
        }
    }
    assert qingtong_seal_regions("does-not-exist.jpg", check) == []


def test_qingtong_only_saves_the_box_as_region_and_evidence(tmp_path):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_customer_seal()]))
    called = []

    result = stage_seal.execute(
        _context(source),
        _request(tmp_path, "qingtong_only"),
        lambda *args, **kwargs: called.append(args),
        api,
    )

    seal_check = result["seal_check"]
    assert seal_check["status"] == "匹配"
    assert [region["role"] for region in seal_check["regions"]] == ["收货客户章"]
    artifacts = result["processing_artifacts"]["seals"]
    assert len(artifacts) == 1
    assert artifacts[0]["original_url"].startswith("/files/artifacts/token/seals/")
    crop = tmp_path / "artifacts" / "seals" / f"qingtong-{artifacts[0]['index']}.png"
    assert crop.is_file()
    # ``qingtong_only`` means no local OCR, and that has not changed.
    assert called == []
    # The preview overlay draws the same box the API judged.
    assert result["seal_regions"] == seal_check["regions"]
    assert api.calls == 1


def test_qingtong_only_keeps_the_dispatch_stamp_out_of_the_regions(tmp_path):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_dispatch_seal(), _customer_seal()]))
    result = stage_seal.execute(
        _context(source),
        _request(tmp_path, "qingtong_only"),
        lambda *args, **kwargs: [],
        api,
    )
    seal_check = result["seal_check"]
    assert len(seal_check["regions"]) == 1
    assert len(seal_check["dual_check"]["excluded_candidates"]) == 1
    artifacts = result["processing_artifacts"]["seals"]
    assert len(artifacts) == 1
    # The dispatch stamp took API index 0, but the one kept stamp is the first
    # seal the reviewer sees, so numbering and the file name start at zero.
    assert artifacts[0]["index"] == 0
    assert artifacts[0]["api_index"] == 1
    assert (tmp_path / "artifacts" / "seals" / "qingtong-0.png").is_file()


def test_qingtong_only_offline_api_produces_no_region(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi({"ok": False, "enabled": True, "message": "清瞳接口超时"})
    monkeypatch.setattr(stage_seal, "detect_seal_regions", _forbidden("detect_seal_regions"))
    result = stage_seal.execute(
        _context(source),
        _request(tmp_path, "qingtong_only"),
        lambda *args, **kwargs: [],
        api,
    )
    seal_check = result["seal_check"]
    assert seal_check["status"] == "识别失败"
    assert seal_check["regions"] == []
    assert result["processing_artifacts"]["seals"] == []


def _forbidden(name):
    def fail(*args, **kwargs):
        raise AssertionError(f"{name} should not be called")

    return fail


def test_dual_mode_recognises_local_seals_inside_the_qingtong_box(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_customer_seal()]))
    monkeypatch.setattr(stage_seal, "detect_seal_regions", _forbidden("detect_seal_regions"))
    captured = {}

    def fake_recognize(source_arg, rows, regions, *args, **kwargs):
        captured["rows"] = rows
        captured["regions"] = [region.to_dict() for region in regions]
        return ["广州花东仓收货专用章"], [{"index": 0, "original_url": "/files/x.png"}]

    result = stage_seal.execute(
        _context(source, _footer_rows()),
        _request(tmp_path, "qingtong"),
        fake_recognize,
        api,
    )

    # The printed requirement row is still stripped before the seal pass sees it.
    assert captured["rows"] == []
    assert len(captured["regions"]) == 1
    assert (captured["regions"][0]["x"], captured["regions"][0]["y"]) == pytest.approx(
        (0.25, 200 / 600)
    )
    seal_check = result["seal_check"]
    assert seal_check["local_evidence"]["status"] == "匹配"
    assert seal_check["recognition_mode"] == "qingtong"
    assert len(seal_check["regions"]) == 1
    assert result["processing_artifacts"]["seals"] == [{"index": 0, "original_url": "/files/x.png"}]


def test_dual_mode_still_detects_when_the_api_returns_no_box(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi({"ok": True, "enabled": True, "response": {"data": {}}})
    monkeypatch.setattr(
        stage_seal, "detect_seal_regions", lambda source: [_detected_region()]
    )
    seen = {}

    def fake_recognize(source_arg, rows, regions, *args, **kwargs):
        seen["roles"] = [region.role for region in regions]
        return [], []

    stage_seal.execute(
        _context(source, _footer_rows()),
        _request(tmp_path, "qingtong"),
        fake_recognize,
        api,
    )
    assert seen["roles"] == ["收货客户章"]


def test_local_mode_keeps_its_own_detection(tmp_path, monkeypatch):
    source = _write_image(tmp_path / "page.jpg")
    monkeypatch.setattr(
        stage_seal, "detect_seal_regions", lambda source: [_detected_region()]
    )
    seen = {}

    def fake_recognize(source_arg, rows, regions, *args, **kwargs):
        seen["roles"] = [region.role for region in regions]
        return [], []

    api = _SealApi(_external([_customer_seal()]))
    stage_seal.execute(
        _context(source, _footer_rows()),
        _request(tmp_path, "local"),
        fake_recognize,
        api,
    )
    assert seen["roles"] == ["收货客户章"]
    # A local-only run must never reach for the network.
    assert api.calls == 0


def _detected_region():
    from receipt_ocr.image_processing import SealRegion

    return SealRegion(
        x=0.2, y=0.3, width=0.3, height=0.2, color="red",
        role="收货客户章", pixel_ratio=0.4,
    )


def test_qingtong_only_artifacts_are_optional_without_an_artifact_dir(tmp_path):
    source = _write_image(tmp_path / "page.jpg")
    request = StageRequest(
        dict(page="vision", date="vision", seal="vision"),
        {"签章要求": "广州花东仓收货专用章"},
        None,
        "",
        "qingtong_only",
    )
    result = stage_seal.execute(
        _context(source), request, lambda *a, **k: [], _SealApi(_external([_customer_seal()]))
    )
    assert result["processing_artifacts"]["seals"] == []
    assert len(result["seal_check"]["regions"]) == 1


def test_seal_api_future_is_preferred_over_a_second_request(tmp_path):
    source = _write_image(tmp_path / "page.jpg")
    api = _SealApi(_external([_customer_seal()]))
    future = SimpleNamespace(result=lambda: _external([_customer_seal()]))
    request = StageRequest(
        dict(page="vision", date="vision", seal="vision"),
        {"签章要求": "广州花东仓收货专用章"},
        tmp_path / "artifacts",
        "/files/artifacts/token",
        "qingtong_only",
        future,
    )
    result = stage_seal.execute(_context(source), request, lambda *a, **k: [], api)
    assert api.calls == 0
    assert len(result["seal_check"]["regions"]) == 1
