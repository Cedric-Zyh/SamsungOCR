"""Exercise the actual recovery hook and its evidence-exclusion boundaries."""
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import receiving
from receipt_ocr.stages.seal import _ellipse_display_texts, _round_stamp_candidates


SUBJECT = "上海朱泾DC021DC0"
PURPOSE = "收货专用章①"
IDENTITY = SUBJECT + PURPOSE


def physical_rows(texts):
    return [{"row_index": index, "text": text, "confidence": .99,
             "variants": [{"source": kind, "text": text, "confidence": .99}
                          for kind in ["color", "gray"]]}
            for index, text in enumerate(texts)]


def form_rows():
    return physical_rows([SUBJECT, PURPOSE, "收货人：刘延", "日期2026年1月31日"])


def stamp(*, frame=True, subject=SUBJECT):
    artifact = {
        "index": 0, "region_id": "seal-0", "color": "red", "shape": "矩形" if frame else "椭圆",
        "shape_code": "rectangle" if frame else "ellipse", "inputs": [], "errors": [],
        "original_url": "/files/test/seal-0-original.png", "reads": [],
        "unwrapped_text": subject, "round_type_band_text": PURPOSE,
        "rectangular_text": subject + PURPOSE,
    }
    if frame:
        artifact.update(rectangular_layout="frame_rows", rectangular_fixed_rows=[
            {"crop_box": [0, index*80, 900, (index+1)*80], "coordinate_space": "oriented"}
            for index in range(4)])
        artifact["reads"] = [dict(row, channel="body", rectangle_row_index=row["row_index"])
                             for row in physical_rows([subject, PURPOSE, "收货人", "日期"])]
    else:
        artifact["reads"] = [{"channel": "center", "text": "收货专用章", "confidence": .99}]
    return artifact


def install_read_boundaries(monkeypatch, *, primary=None, detections=(), directions=(), accepted=False):
    """Only OCR/image boundaries are stubbed; the hook and decision code run."""
    monkeypatch.setattr(receiving, "read_primary", lambda *args: (deepcopy(primary or []), deepcopy(detections)))
    monkeypatch.setattr(receiving, "prepare_receiving_grid", lambda *args: {
        "accepted": accepted, "reason": "test_geometry", "rows": [],
        "whole_color_url": "/files/test/grid-color.png", "whole_gray_url": "/files/test/grid-gray.png"})
    monkeypatch.setattr(receiving, "read_grid", lambda *args: deepcopy(directions))
    monkeypatch.setattr(receiving, "_read_split", lambda *args: None)


def run_hook(artifact, tmp_path, *, prefix="/files/test"):
    return receiving.recognize_receiving_form(
        artifact, tmp_path, prefix, "paddle_v6", object(), orientation_mode="combined")


def test_same_direction_proven_form_recovers_one_complete_identity(monkeypatch, tmp_path):
    artifact = stamp(frame=False)
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": form_rows()}], accepted=True)
    assert run_hook(artifact, tmp_path) == [IDENTITY]
    form = artifact["receiving_form"]
    assert form["managed"] and form["status"] == "adopted"
    assert form["selected_source"] == "verified_grid_recovery"
    assert artifact["shape_code"] == "rectangle"
    assert artifact["shape_before_receiving_grid"] == "椭圆"
    assert all(read["matching"] is False for read in artifact["reads"])


def test_caption_from_opposite_direction_cannot_prove_a_receiving_form(monkeypatch, tmp_path):
    artifact = stamp(frame=False)
    directions = [
        {"direction": 0, "rows": physical_rows([SUBJECT, PURPOSE, "专用编号ABC", "验收组"])},
        {"direction": 180, "rows": physical_rows(["日期", "倒置噪声", "其他片段", "未读"])},
    ]
    install_read_boundaries(monkeypatch, directions=directions, accepted=True)
    assert run_hook(artifact, tmp_path) is None
    assert not artifact.get("receiving_form", {}).get("managed")


def test_static_post_purpose_identifiers_cannot_be_discarded_as_a_form(monkeypatch, tmp_path):
    artifact = stamp(frame=False)
    rows = physical_rows([SUBJECT, PURPOSE, "专用编号ABC", "日期2026年1月31日"])
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": rows}], accepted=True)
    assert run_hook(artifact, tmp_path) is None
    assert "receiving_form" not in artifact


def test_subject_starting_with_date_does_not_prove_a_value_caption(monkeypatch, tmp_path):
    artifact = stamp(subject="日期技术有限公司")
    rows = physical_rows(["日期技术有限公司", PURPOSE, "部门编号ABC", "验收组"])
    install_read_boundaries(monkeypatch, primary=rows)
    before = deepcopy(artifact)
    assert run_hook(artifact, tmp_path) is None
    assert artifact == before


def test_a_verified_blank_value_cell_can_accompany_an_observed_caption(monkeypatch, tmp_path):
    artifact = stamp(frame=False)
    rows = physical_rows([SUBJECT, PURPOSE, "收货人：刘延", ""])
    rows[-1].update(confidence=0, is_blank=True, blank_check={"blank": True})
    for variant in rows[-1]["variants"]: variant["confidence"] = 0
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": rows}], accepted=True)
    assert run_hook(artifact, tmp_path) == [IDENTITY]


@pytest.mark.parametrize("date_text", ["", "期", "日", "2026年1月31日", "旧期2026"])
def test_unread_date_is_allowed_without_being_assumed_blank(monkeypatch, tmp_path, date_text):
    artifact = stamp(frame=False)
    rows = physical_rows([SUBJECT, PURPOSE, "收货人：刘延", date_text])
    if not date_text:
        rows[-1]["confidence"] = 0
        for variant in rows[-1]["variants"]: variant["confidence"] = 0
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": rows}], accepted=True)
    assert run_hook(artifact, tmp_path) == [IDENTITY]
    saved = artifact["receiving_form"]["grid"]["directions"][0]["rows"][-1]
    assert saved["text"] == date_text and saved["ignored_date"] is True
    assert saved.get("is_blank") is not True
    assert (saved.get("blank_check") or {}).get("blank") is not True


def test_independent_caption_detection_proves_the_corresponding_primary_cell(monkeypatch, tmp_path):
    artifact = stamp()
    rows = physical_rows([SUBJECT, PURPOSE, "收货人：刘延", "旧期2026"])
    mapped = [{"row_index": 3, "text": "日期", "confidence": .99}]
    install_read_boundaries(monkeypatch, primary=rows, detections=mapped)
    assert run_hook(artifact, tmp_path) == [IDENTITY]


def test_form_proof_cannot_borrow_a_caption_mapped_to_another_cell(monkeypatch, tmp_path):
    artifact = stamp()
    rows = physical_rows([SUBJECT, PURPOSE, "收货人是异", "未知内容ABC"])
    mapped = [{"row_index": 2, "text": "日期", "confidence": .99}]
    install_read_boundaries(monkeypatch, primary=rows, detections=mapped)
    assert run_hook(artifact, tmp_path) is None


def test_optional_split_failure_does_not_restore_a_conflicting_exact_old_string(monkeypatch, tmp_path):
    artifact = stamp()
    rows = form_rows()
    rows[0]["variants"][1]["text"] = SUBJECT.replace("DC0", "DCO")
    install_read_boundaries(monkeypatch, primary=rows)
    def failed_split(*args):
        raise OSError("test split image write failed")
    monkeypatch.setattr(receiving, "_read_split", failed_split)
    assert run_hook(artifact, tmp_path) == []
    assert artifact["receiving_form"]["status"] == "conflict"
    assert artifact["receiving_form"]["reason"] == "primary_meaningful_conflict"
    assert artifact["receiving_form"]["split"]["reason"] == "split_recovery_failed"
    assert artifact["matching_texts"] == []
    assert any(error["step"] == "receiving_split" for error in artifact["errors"])


@pytest.mark.parametrize("failed_step", ["prepare_receiving_grid", "read_grid"])
def test_optional_grid_failure_keeps_the_already_computed_primary_conflict(monkeypatch, tmp_path, failed_step):
    artifact = stamp()
    rows = form_rows()
    rows[0]["variants"][1]["text"] = SUBJECT.replace("DC0", "DCO")
    install_read_boundaries(monkeypatch, primary=rows, accepted=True)
    def failed_grid(*args):
        raise OSError("test optional grid failure")
    monkeypatch.setattr(receiving, failed_step, failed_grid)
    assert run_hook(artifact, tmp_path) == []
    assert artifact["receiving_form"]["status"] == "conflict"
    assert any(error["step"] == "receiving_grid" for error in artifact["errors"])


def test_optional_grid_failure_preserves_a_complete_existing_primary(monkeypatch, tmp_path):
    artifact = stamp()
    install_read_boundaries(monkeypatch, primary=form_rows())
    def failed_grid(*args):
        raise OSError("test optional grid failure")
    monkeypatch.setattr(receiving, "prepare_receiving_grid", failed_grid)
    assert run_hook(artifact, tmp_path) == [IDENTITY]
    assert artifact["receiving_form"]["selected_source"] == "primary"


def test_empty_url_prefix_reads_local_row_paths_and_preserves_identity(monkeypatch, tmp_path):
    artifact = stamp()
    artifact["original_url"] = ""
    artifact["rectangular_row_urls"] = [""]*4
    values = [SUBJECT, PURPOSE, "收货人：刘延", "日期2026年1月31日"]
    for index, prepared in enumerate(artifact["rectangular_fixed_rows"]):
        path = tmp_path / f"seal-0-rectangular-row-{index}.png"
        Image.new("RGB", (260, 100), "white").save(path)
        prepared["path"] = str(path)
    Image.new("RGB", (260, 400), "white").save(tmp_path/"seal-0-original.png")
    seen = []
    def rejected_grid(source, *args):
        assert Path(source) == tmp_path/"seal-0-original.png"
        assert Path(source).is_file()
        return {"accepted": False, "reason": "not_needed_for_this_test"}
    class Reader:
        def read_line(self, path, **kwargs):
            seen.append(Path(path))
            index = int(Path(path).stem.rsplit("-", 1)[-1])
            return [TextObservation(values[index], .99, 0, 0, 1, 1)]
    monkeypatch.setattr(receiving, "prepare_receiving_grid", rejected_grid)
    result = receiving.recognize_receiving_form(
        artifact, tmp_path, "", "paddle_v6", Reader(), orientation_mode="combined")
    assert result == [IDENTITY] and len(seen) == 4
    assert all(path.is_file() for path in seen)
    assert artifact["receiving_form"]["managed"]


def test_an_ordinary_non_receiving_stamp_falls_back_without_extra_ocr(monkeypatch, tmp_path):
    artifact = stamp()
    artifact["reads"] = [{"text": "上海测试有限公司业务专用章", "confidence": .99, "channel": "body"}]
    artifact.pop("round_type_band_text")
    def unexpected(*args):
        pytest.fail("A non-receiving stamp must retain its existing route")
    grid_checks = []
    def rejected_geometry(*args):
        grid_checks.append(args[0])
        return {"accepted": False, "reason": "no_measured_receiving_grid"}
    monkeypatch.setattr(receiving, "read_primary", unexpected)
    monkeypatch.setattr(receiving, "read_grid", unexpected)
    monkeypatch.setattr(receiving, "prepare_receiving_grid", rejected_geometry)
    before = deepcopy(artifact)
    assert run_hook(artifact, tmp_path) is None
    assert artifact == before
    assert len(grid_checks) == 1


@pytest.mark.parametrize("change", ["blue", "other_provider", "orientation_none"])
def test_unsupported_route_keeps_existing_evidence(monkeypatch, tmp_path, change):
    artifact = stamp()
    if change == "blue": artifact["color"] = "blue"
    before = deepcopy(artifact)
    result = receiving.recognize_receiving_form(
        artifact, tmp_path, "/files/test", "qingtong" if change == "other_provider" else "paddle_v6",
        object(), orientation_mode="none" if change == "orientation_none" else "combined")
    assert result is None and artifact == before


def test_managed_round_channels_cannot_reassemble_a_blocked_candidate():
    artifact = stamp(frame=False)
    artifact["receiving_form"] = {"managed": True, "status": "conflict", "identity_text": ""}
    assert _round_stamp_candidates([], [artifact]) == []
    assert _ellipse_display_texts([], [artifact]) == []
    assert _round_stamp_candidates([IDENTITY], [artifact]) == [IDENTITY]


def test_another_round_stamp_remains_independent_of_the_managed_one():
    blocked = stamp(frame=False)
    blocked["receiving_form"] = {"managed": True, "status": "conflict", "identity_text": ""}
    other = {"index": 1, "shape": "圆形", "unwrapped_text": "另一客户有限公司",
             "round_type_band_text": PURPOSE}
    candidates = _round_stamp_candidates([], [blocked, other])
    assert candidates == ["另一客户有限公司" + PURPOSE]
    assert IDENTITY not in candidates



@pytest.mark.parametrize("static_text", ["日期ABC", "日期DC021", "验收组", "专用编号ABC"])
def test_date_value_content_does_not_participate_in_identity(monkeypatch, tmp_path, static_text):
    artifact = stamp(frame=False)
    rows = physical_rows([SUBJECT, PURPOSE, "收货人：刘延", static_text])
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": rows}], accepted=True)
    assert run_hook(artifact, tmp_path) == [IDENTITY]
    assert artifact["matching_texts"] == [IDENTITY]
    assert static_text not in artifact["receiving_form"]["identity_text"]


@pytest.mark.parametrize("extra_text", ["验收组", "", "12345", "期"])
def test_an_unknown_third_post_purpose_cell_cannot_be_dropped(monkeypatch, tmp_path, extra_text):
    artifact = stamp(frame=False)
    rows = physical_rows([SUBJECT, PURPOSE, "收货人：刘延", "日期", extra_text])
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": rows}], accepted=True)
    assert run_hook(artifact, tmp_path) is None
    assert "receiving_form" not in artifact


def test_date_captions_cannot_borrow_a_recipient_from_the_reverse_direction(monkeypatch, tmp_path):
    artifact = stamp(frame=False)
    directions = [
        {"direction": 0, "rows": physical_rows([SUBJECT, PURPOSE, "日期", "日期2026年1月31日"])},
        {"direction": 180, "rows": physical_rows(["倒置噪声", "另一片段", "收货人：刘延", "期"])},
    ]
    install_read_boundaries(monkeypatch, directions=directions, accepted=True)
    assert run_hook(artifact, tmp_path) is None
    assert "receiving_form" not in artifact


@pytest.mark.parametrize("support_source", ["row", "variant", "detection"])
def test_differing_date_reads_do_not_change_stamp_identity(monkeypatch, tmp_path, support_source):
    artifact = stamp()
    rows = physical_rows([SUBJECT, PURPOSE, "收货人：刘延", "日期ABC"])
    detections = []
    if support_source == "row":
        rows[-1]["text"] = "日期"
    elif support_source == "variant":
        rows[-1]["variants"][0]["text"] = "日期"
    else:
        detections = [{"row_index": 3, "text": "日期", "confidence": .99}]
    install_read_boundaries(monkeypatch, primary=rows, detections=detections)
    assert run_hook(artifact, tmp_path) == [IDENTITY]
    assert artifact["matching_texts"] == [IDENTITY]
    assert "日期ABC" not in artifact["receiving_form"]["identity_text"]



def ellipse_without_verified_purpose():
    """Reduced upstream state from the actual 7330231171 full-image replay."""
    artifact = stamp(frame=False)
    artifact.pop("round_type_band_text")
    artifact.pop("rectangular_text")
    artifact["unwrapped_text"] = "NENO"
    artifact["reads"] = [{"id": "seal-0:ring:0", "input_id": "seal-0:ring",
                          "region_id": "seal-0", "channel": "ring", "provider": "paddle_v6",
                          "text": "NENO", "confidence": .4369213283061981}]
    artifact["source_orientation_attempt"] = {
        "mode": "combined", "anchor_text": "", "confidence": 0,
        "four_way_candidates": {"0": {"text": "收货专用早", "anchor_text": "收货专用章",
                                       "confidence": .844, "post_rotation_verified": False}},
    }
    return artifact


def test_measured_grid_can_recover_upstream_ellipse_without_any_old_verified_purpose(monkeypatch, tmp_path):
    artifact = ellipse_without_verified_purpose()
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": form_rows()}], accepted=True)
    events = []
    measure_grid, read_grid = receiving.prepare_receiving_grid, receiving.read_grid
    def geometry_first(*args):
        events.append("geometry")
        return measure_grid(*args)
    def primary_after_geometry(*args):
        assert events == ["geometry"]
        events.append("primary")
        return [], []
    def grid_ocr_after_geometry(*args):
        assert events == ["geometry", "primary"]
        events.append("grid_ocr")
        return read_grid(*args)
    monkeypatch.setattr(receiving, "prepare_receiving_grid", geometry_first)
    monkeypatch.setattr(receiving, "read_primary", primary_after_geometry)
    monkeypatch.setattr(receiving, "read_grid", grid_ocr_after_geometry)
    assert all("收货专用章" not in row["text"] for row in artifact["reads"])
    assert run_hook(artifact, tmp_path) == [IDENTITY]
    form = artifact["receiving_form"]
    assert form["managed"] and form["status"] == "adopted"
    assert form["selected_source"] == "verified_grid_recovery"
    assert artifact["shape_before_receiving_grid"] == "椭圆"
    assert artifact["shape_code"] == "rectangle"
    assert artifact["matching_texts"] == [IDENTITY]
    assert events == ["geometry", "primary", "grid_ocr"]


def test_rejected_geometry_without_old_purpose_keeps_original_route_without_ocr(monkeypatch, tmp_path):
    artifact = ellipse_without_verified_purpose()
    seen = []
    def reject_geometry(source, *args):
        seen.append(Path(source))
        return {"accepted": False, "reason": "no_complete_physical_grid"}
    def unexpected_ocr(*args):
        pytest.fail("A rejected geometric candidate must never trigger row OCR")
    monkeypatch.setattr(receiving, "prepare_receiving_grid", reject_geometry)
    monkeypatch.setattr(receiving, "read_primary", unexpected_ocr)
    monkeypatch.setattr(receiving, "read_grid", unexpected_ocr)
    monkeypatch.setattr(receiving, "_read_split", unexpected_ocr)
    before = deepcopy(artifact)
    assert run_hook(artifact, tmp_path) is None
    assert artifact == before
    assert seen == [tmp_path / "seal-0-original.png"]


@pytest.mark.parametrize("unverified_purpose", ["业务专用章", "收货专用早", ""])
def test_accepted_grid_needs_actual_purpose_even_if_old_orientation_has_an_anchor(monkeypatch, tmp_path, unverified_purpose):
    artifact = ellipse_without_verified_purpose()
    rows = physical_rows([SUBJECT, unverified_purpose, "收货人：刘延", "日期"])
    install_read_boundaries(monkeypatch, directions=[{"direction": 0, "rows": rows}], accepted=True)
    before = deepcopy(artifact)
    assert run_hook(artifact, tmp_path) is None
    assert artifact == before
    assert "receiving_form" not in artifact



def test_failed_geometry_without_old_purpose_keeps_original_route_without_ocr(monkeypatch, tmp_path):
    artifact = ellipse_without_verified_purpose()
    def failed_geometry(*args):
        raise OSError("test measured image is unavailable")
    def unexpected_ocr(*args):
        pytest.fail("A failed geometry measurement must not trigger row OCR")
    monkeypatch.setattr(receiving, "prepare_receiving_grid", failed_geometry)
    monkeypatch.setattr(receiving, "read_primary", unexpected_ocr)
    monkeypatch.setattr(receiving, "read_grid", unexpected_ocr)
    before = deepcopy(artifact)
    assert run_hook(artifact, tmp_path) is None
    assert artifact == before
