import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.ring_seam import save_ring_with_blank_seam
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.preprocess import inputs


def _strip(gap=(220, 420)):
    image = np.full((100, 1000, 3), 255, np.uint8)
    # Dense glyph bodies with ordinary spaces, plus a continuous outer rim.
    image[:5] = 80
    for x in range(0, 1000, 40):
        image[30:90, x:x + 25] = 40
    image[22:95, gap[0]:gap[1]] = 255
    # Pale pixels below the localization threshold must survive unchanged.
    image[96:99, 297:305] = (230, 238, 249)
    return image


@pytest.mark.parametrize("scale", [.5, 1, 2])
def test_wide_gap_shift_preserves_every_pixel_and_source(tmp_path, scale):
    image = cv2.resize(_strip(), None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    source, target = tmp_path / "source.png", tmp_path / "shifted.png"
    cv2.imwrite(str(source), image)
    source_bytes = source.read_bytes()
    result = save_ring_with_blank_seam(source, target)
    assert result["applied"]
    assert 220 * scale < result["shift"] < 420 * scale
    shifted = cv2.imread(str(target))
    assert np.array_equal(np.roll(shifted, result["shift"], axis=1), image)
    assert source.read_bytes() == source_bytes


@pytest.mark.parametrize("kind", ["ordinary_space", "edge_gap", "white", "solid", "tiny"])
def test_ambiguous_empty_and_already_aligned_inputs_remain_untouched(tmp_path, kind):
    image = _strip((220, 280))
    if kind == "edge_gap":
        image = np.roll(_strip(), -320, axis=1)
    elif kind == "white":
        image[:] = 255
    elif kind == "solid":
        image[:] = 0
    elif kind == "tiny":
        image = np.full((1, 1, 3), 255, np.uint8)
    source, target = tmp_path / "source.png", tmp_path / "shifted.png"
    cv2.imwrite(str(source), image)
    source_bytes = source.read_bytes()
    result = save_ring_with_blank_seam(source, target)
    assert not result["applied"]
    assert result["shift"] == 0
    assert not target.exists()
    assert source.read_bytes() == source_bytes


@pytest.mark.parametrize("shape", ["round", "ellipse"])
@pytest.mark.parametrize("gap", [(220, 420), (220, 280)])
def test_ocr_and_preview_use_the_same_final_seam_image(monkeypatch, tmp_path, shape, gap):
    source = tmp_path / "source.png"
    cv2.imwrite(str(source), np.full((200, 200, 3), 255, np.uint8))
    strip = _strip(gap)
    monkeypatch.setattr(inputs, "classify_shape", lambda *_: shape)
    monkeypatch.setattr(inputs, "trace_ellipse_border", lambda *a, **kw: None)
    monkeypatch.setattr(inputs, "save_unwrapped_seal",
                        lambda source, dest, *a, **kw: cv2.imwrite(str(dest), strip))
    # Exercise the company-derivative handoff as well as the full ring path.
    def select_company(source, dest):
        cv2.imwrite(str(dest), cv2.imread(str(source)))
        return {"method": "test_selection"}
    monkeypatch.setattr(inputs, "save_company_ring_input", select_company)
    seen = []

    class Reader:
        def read_line(self, path, *, provider):
            seen.append(path)
            return [TextObservation("海口全人通讯器材有限公司", .98, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    _, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, "red", "客户章", 0)],
        tmp_path / "artifacts", "/files/test", "paddle_v6", orientation_mode="none",
    )
    artifact = artifacts[0]
    assert not artifact["errors"]
    assert len(seen) == 1
    assert artifact["reads"][0]["image_url"] == artifact["unwrapped_url"]
    assert artifact["unwrapped_url"].endswith("/" + seen[0].name)
    adjustment = artifact["ring_seam_adjustment"]
    if gap == (220, 420):
        assert adjustment["applied"]
        assert seen[0].name == "seal-0-ring-seam.png"
        assert np.array_equal(np.roll(cv2.imread(str(seen[0])), adjustment["shift"], axis=1), strip)
        before = next(x for x in artifact["inputs"] if x["name"] == "ring-before-seam")
        assert not before["ocr_input"]
        assert before["image_url"] == artifact["unwrapped_before_seam_url"]
        expected_name = "seal-0-ring-company.png" if shape == "round" else "seal-0-ring.png"
        assert before["image_url"].endswith("/" + expected_name)
    else:
        assert not adjustment["applied"]
        assert "unwrapped_before_seam_url" not in artifact
        assert np.array_equal(cv2.imread(str(seen[0])), strip)


def test_seam_failure_keeps_original_ocr_job(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    cv2.imwrite(str(source), np.full((200, 200, 3), 255, np.uint8))
    monkeypatch.setattr(inputs, "classify_shape", lambda *_: "round")
    monkeypatch.setattr(inputs, "save_unwrapped_seal",
                        lambda source, dest, *a, **kw: cv2.imwrite(str(dest), _strip()))
    monkeypatch.setattr(inputs, "save_company_ring_input", lambda *a: None)
    def fail(*args):
        raise OSError("test write failure")
    monkeypatch.setattr(inputs, "save_ring_with_blank_seam", fail)
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, "red", "客户章", 0), 0,
        tmp_path / "artifacts", "/files/test", "paddle_v6", "none",
    )
    assert len(jobs) == 1
    assert jobs[0][1].name == "seal-0-ring.png"
    assert artifact["errors"] == [{"step": "ring_seam", "message": "test write failure"}]
