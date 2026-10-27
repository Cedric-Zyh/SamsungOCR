"""Round-rim recovery must improve measured geometry without editing source ink."""
import shutil

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging import round_rim
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.unwrap import save_ring_text_band_preview, save_unwrapped_seal
from receipt_ocr.recognition.seal.preprocess import inputs
from receipt_ocr.recognition.seal.preprocess.round_rim import prepare_round_rim


def stamp(*, polluted=True, color="red", count=24, inner=.62):
    image = np.full((600, 600, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    ink = (10, 10, 245) if color == "red" else (245, 10, 10)
    cv2.circle(image, (300, 300), 240, ink, 8)
    for angle in np.linspace(190, 510, count):
        theta = np.deg2rad(angle)
        radial = np.array([np.cos(theta), np.sin(theta)])
        tangent = np.array([-np.sin(theta), np.cos(theta)])
        points = [np.array([300, 300])+240*f*radial+w*tangent
                  for f, w in ((.88, -12), (.88, 12), (inner, 12), (inner, -12))]
        polygon = np.rint(points).astype(np.int32)
        cv2.polylines(image, [polygon], True, ink, 4)
        cv2.polylines(glyphs, [polygon], True, 255, 4)
    if polluted:
        y, x = np.indices(image.shape[:2])
        distance = np.hypot(x-300, y-300)
        angle = np.arctan2(y-300, x-300)
        smear = ((distance > 235) & (distance < 250+20*np.sin(3*angle)+6*np.sin(7*angle))
                 & (image[:, :, 0 if color == "red" else 2] > 200))
        image[smear] = (230, 230, 255) if color == "red" else (255, 230, 230)
    return image, glyphs


@pytest.mark.parametrize("color", ["red", "blue"])
def test_pale_smear_recovery_improves_held_out_border_and_keeps_complete_glyphs(color):
    intact, glyphs = stamp(color=color)
    masked = intact.copy()
    cv2.rectangle(intact, (240, 270), (360, 330), (0, 0, 220), -1)
    before, masked_before = intact.copy(), masked.copy()
    guide, evidence = round_rim.calibrate_round_ring(intact, masked, color=color)
    assert evidence["status"] == "applied"
    assert evidence["base_fit_error"] > .025
    assert evidence["outer"]["validation_median_px"] < evidence["base_validation_median_px"]*.75
    assert np.linalg.norm(np.subtract(guide.annulus[0][2][0], (300, 300))) < 3
    assert evidence["outer"]["coverage"] > .9
    assert evidence["inner"]["boundary_kind"] == "conservative_sampling_envelope"
    assert evidence["inner"]["safety_margin_px"] >= 8
    sampled = cv2.remap(glyphs, guide.map_x, guide.map_y, cv2.INTER_NEAREST)
    assert sampled.any() and not sampled[0].any() and not sampled[-1].any()
    assert np.array_equal(intact, before) and np.array_equal(masked, masked_before)


def test_ordinary_fit_skips_expensive_probe(monkeypatch):
    image, _ = stamp(polluted=False)
    def unexpected(*args):
        raise AssertionError("stable ordinary fits must not run the calibration probe")
    monkeypatch.setattr(round_rim, "_observed_outer", unexpected)
    guide, evidence = round_rim.calibrate_round_ring(image, image)
    assert guide is None and evidence["status"] == "skipped"


@pytest.mark.parametrize("kind", ["blank", "wrong_frame", "unsupported_color"])
def test_missing_or_incompatible_inputs_fail_closed(kind):
    image, _ = stamp()
    masked, color = image, "red"
    if kind == "blank":
        image = masked = np.full_like(image, 255)
    elif kind == "wrong_frame":
        masked = image[:-1]
    else:
        color = "black"
    guide, evidence = round_rim.calibrate_round_ring(image, masked, color=color)
    assert guide is None and evidence["status"] == "fallback"


def test_unstable_strength_geometry_is_rejected(monkeypatch):
    image, _ = stamp()
    original = round_rim._fit_ellipse_annulus
    calls = []
    def fit(mask):
        geometry = original(mask)
        calls.append(mask)
        if len(calls) == 2:
            outer, inner, estimated = geometry
            (_, cy), axes, angle = outer[2]
            return (outer[0], outer[1], ((400., cy), axes, angle)), inner, estimated
        return geometry
    monkeypatch.setattr(round_rim, "_fit_ellipse_annulus", fit)
    guide, evidence = round_rim.calibrate_round_ring(image, image)
    assert guide is None and evidence["reason"] == "unstable_strength_geometry"


@pytest.mark.parametrize("count,inner,reason", [
    (4, .62, "insufficient_glyph_support"), (16, .40, "ink_reaches_probe_limit"),
])
def test_uncertain_inner_measurement_preserves_existing_route(count, inner, reason):
    image, _ = stamp(count=count, inner=inner)
    guide, evidence = round_rim.calibrate_round_ring(image, image)
    assert guide is None and evidence["reason"] == reason


def test_inner_measurement_uses_masked_pixels_in_the_intact_coordinate_frame(monkeypatch):
    intact, _ = stamp()
    masked = intact.copy()
    cv2.rectangle(intact, (260, 280), (340, 320), (0, 0, 230), -1)
    original = round_rim._letter_edge_maps
    seen = []
    def measure(gray, geometry):
        seen.append(gray.copy())
        assert np.all(gray[280:321, 260:341] == 255)
        return original(gray, geometry)
    monkeypatch.setattr(round_rim, "_letter_edge_maps", measure)
    guide, evidence = round_rim.calibrate_round_ring(intact, masked)
    assert guide is not None and len(seen) == 1


def test_no_improvement_on_held_out_angles_keeps_existing_route(monkeypatch):
    image, _ = stamp(polluted=False)
    original = round_rim._fit_ellipse_annulus
    calls = []
    def fit(mask):
        geometry = original(mask)
        calls.append(mask)
        if len(calls) == 1:
            outer, inner, estimated = geometry
            return (outer[0], .04, outer[2]), inner, estimated
        return geometry
    monkeypatch.setattr(round_rim, "_fit_ellipse_annulus", fit)
    guide, evidence = round_rim.calibrate_round_ring(image, image)
    assert guide is None and evidence["reason"] == "no_validated_improvement"


def test_saved_band_and_ocr_use_one_guide_and_preserve_source_files(tmp_path):
    image, _ = stamp()
    source = tmp_path/"intact.png"
    masked = tmp_path/"masked.png"
    cv2.imwrite(str(source), image)
    cv2.imwrite(str(masked), image)
    originals = source.read_bytes(), masked.read_bytes()
    result, evidence = prepare_round_rim(source, masked, tmp_path/"out/ring.png", color="red")
    assert result is not None and evidence["status"] == "applied"
    expected_ring, expected_band = tmp_path/"expected-ring.png", tmp_path/"expected-band.png"
    save_unwrapped_seal(masked, expected_ring, None, color="red", elliptical=True,
                        normalized=True, single_line=True, ring_guide=result["guide"])
    save_ring_text_band_preview(masked, expected_band, ring_guide=result["guide"])
    assert result["ring"].read_bytes() == expected_ring.read_bytes()
    assert result["band"].read_bytes() == expected_band.read_bytes()
    assert (source.read_bytes(), masked.read_bytes()) == originals


def test_pipeline_retains_original_and_sends_calibrated_image_to_ocr(tmp_path, monkeypatch):
    image, _ = stamp()
    source = tmp_path/"source.png"
    cv2.imwrite(str(source), image)
    monkeypatch.setattr(inputs, "save_region_crop", lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, "save_color_isolated_seal", lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, "classify_shape", lambda *a: "round")
    monkeypatch.setattr(inputs, "enhance_oriented_seal", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "prepare_multi_zone", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "save_company_ring_input", lambda *a: None)
    monkeypatch.setattr(inputs, "recover_number_punctuation", lambda *a, **k: ({}, {"status": "not_needed"}))
    def keep_calibrated_guide(*a, **kwargs):
        assert kwargs["ring_guide"] is not None
        return None
    monkeypatch.setattr(inputs, "prepare_han_ring", keep_calibrated_guide)
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, "red", "客户章", 0), 0,
        tmp_path/"out", "/files/test", "paddle_v6", "none")
    assert artifact["errors"] == []
    assert artifact["round_rim_calibration"]["status"] == "applied"
    assert artifact["ring_geometry"] == "round_observed_rim_letter_band"
    assert artifact["unwrapped_uncalibrated_url"].endswith("/seal-0-ring.png")
    assert artifact["ring_text_band_url"].endswith("/seal-0-ring-calibrated-band.png")
    assert len(jobs) == 1 and jobs[0][0]["channel"] == "ring"
    assert jobs[0][0]["image_url"] == artifact["unwrapped_url"]
    assert "ring-calibrated" in jobs[0][1].name
    old_input = next(item for item in artifact["inputs"] if item["name"] == "ring-uncalibrated")
    assert old_input["ocr_input"] is False


@pytest.mark.parametrize("polluted,count,status", [(True, 4, "fallback"), (False, 24, "skipped")])
def test_pipeline_uncertain_or_ordinary_fit_retains_original_pixels(tmp_path, monkeypatch, polluted, count, status):
    image, _ = stamp(polluted=polluted, count=count)
    source = tmp_path/"source.png"
    cv2.imwrite(str(source), image)
    monkeypatch.setattr(inputs, "save_region_crop", lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, "save_color_isolated_seal", lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, "classify_shape", lambda *a: "round")
    monkeypatch.setattr(inputs, "enhance_oriented_seal", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "prepare_multi_zone", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "prepare_han_ring", lambda *a, **k: None)
    monkeypatch.setattr(inputs, "save_company_ring_input", lambda *a: None)
    monkeypatch.setattr(inputs, "save_ring_with_blank_seam", lambda *a: {"applied": False})
    monkeypatch.setattr(inputs, "recover_number_punctuation", lambda *a, **k: ({}, {"status": "not_needed"}))
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, "red", "客户章", 0), 0,
        tmp_path/"out", "/files/test", "paddle_v6", "none")
    assert artifact["errors"] == []
    assert artifact["round_rim_calibration"]["status"] == status
    assert "unwrapped_uncalibrated_url" not in artifact
    expected = tmp_path/"expected.png"
    save_unwrapped_seal(source, expected, None, color="red", elliptical=True,
                        normalized=True, single_line=True)
    assert jobs[0][1].read_bytes() == expected.read_bytes()
