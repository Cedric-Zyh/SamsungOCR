"""A date-crop window that falls off the page must not abort the date stage.

On 7330644044.jpg the signature-requirement row sits at y=0.736, so the
``far_lower`` window starts at y=1.018 -- past the bottom edge.  The empty
slice reached ``cv2.imencode``, whose assertion turned the whole date stage
into 识别失败 even though the normal crops had already read the date.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from receipt_ocr.date_crop_state import DateCropRun, DateCropServices
from receipt_ocr.image_processing import DateCropOutOfRange, save_receipt_date_crop

# Measured on 7330644044.jpg (2479x3609).
ANCHOR_Y = 0.7362
FAR_LOWER_ANCHOR_Y = ANCHOR_Y + 0.23


def _tall_page(tmp_path: Path) -> Path:
    source = tmp_path / "page.png"
    Image.new("RGB", (620, 900), "white").save(source)
    return source


def test_window_past_the_bottom_edge_is_reported_not_encoded(tmp_path):
    source = _tall_page(tmp_path)
    with pytest.raises(DateCropOutOfRange) as failure:
        save_receipt_date_crop(
            source,
            tmp_path / "crop.png",
            FAR_LOWER_ANCHOR_Y,
            tight=False,
            raw_destination=tmp_path / "raw.jpg",
            color_clean_destination=tmp_path / "clean.png",
        )
    # The message must carry the window so the region can be explained.
    assert "1.018" in str(failure.value)
    assert not (tmp_path / "crop.png").exists()


def test_normal_windows_at_the_same_anchor_are_unchanged(tmp_path):
    """The guard must not fire for the crops that do fit on the page."""
    source = _tall_page(tmp_path)
    for name, anchor, tight in (
        ("tight", ANCHOR_Y, True),
        ("wide", ANCHOR_Y, False),
        ("lower", ANCHOR_Y + 0.055, False),
    ):
        raw = tmp_path / f"{name}-raw.jpg"
        x, y, width, height = save_receipt_date_crop(
            source, tmp_path / f"{name}.png", anchor, tight=tight, raw_destination=raw
        )
        assert (tmp_path / f"{name}.png").is_file()
        assert (raw).is_file()
        assert 0.0 <= y and height > 0 and width > 0 and x >= 0.0


def _run(tmp_path: Path) -> DateCropRun:
    return DateCropRun(
        source=tmp_path / "page.png",
        anchor_y=ANCHOR_Y,
        required_text="",
        artifact_dir=None,
        artifact_url_prefix="",
        ocr_backend="paddle_v6",
        secondary_ocr_backend=None,
        allow_strict_date_without_requirement=False,
        creation_text="",
        temp_dir=tmp_path,
        services=DateCropServices(
            save_receipt_date_crop=save_receipt_date_crop,
            save_date_line_crop=lambda *args, **kwargs: (0.0, 0.0, 1.0, 1.0),
            recognize_text=lambda *args, **kwargs: [],
            backend_label=lambda name: name,
        ),
    )


def test_collect_date_regions_only_processes_compact_window(tmp_path, monkeypatch):
    """The compact date region is the only active recognition window."""
    from receipt_ocr import date_crop_workflow

    prepared: list[str] = []
    processed: list[str] = []

    def prepare(run, region):
        prepared.append(region.crop_key)

    monkeypatch.setattr(date_crop_workflow, "_prepare_date_region", prepare)
    monkeypatch.setattr(
        date_crop_workflow,
        "_recognize_region_variants",
        lambda run, region: processed.append(region.crop_key),
    )
    monkeypatch.setattr(date_crop_workflow, "_recognize_region_lines", lambda run, r: None)
    monkeypatch.setattr(
        date_crop_workflow, "_collect_pending_region_evidence", lambda run, r: None
    )
    monkeypatch.setattr(date_crop_workflow, "_confirm_far_lower_region", lambda run, r: None)
    monkeypatch.setattr(date_crop_workflow, "_publish_date_region", lambda run, r: None)

    run = _run(tmp_path)
    date_crop_workflow.collect_date_regions(run)

    assert prepared == ["tight"]
    assert processed == ["tight"]


def test_bottom_anchor_does_not_enable_extended_windows(tmp_path, monkeypatch):
    """A different anchor does not re-enable non-compact date windows."""
    from receipt_ocr import date_crop_workflow

    prepared: list[str] = []
    monkeypatch.setattr(
        date_crop_workflow,
        "_prepare_date_region",
        lambda run, region: prepared.append(region.crop_key),
    )
    for name in (
        "_recognize_region_variants",
        "_recognize_region_lines",
        "_collect_pending_region_evidence",
        "_confirm_far_lower_region",
        "_publish_date_region",
    ):
        monkeypatch.setattr(date_crop_workflow, name, lambda run, region: None)

    run = _run(tmp_path)
    run.anchor_y = 0.30
    date_crop_workflow.collect_date_regions(run)

    assert prepared == ["tight"]
