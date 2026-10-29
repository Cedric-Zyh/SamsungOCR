from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.crops import save_color_isolated_seal
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import rotate_stamp_image
from receipt_ocr.recognition.seal.preprocess.red_enhancement import enhance_oriented_red_seal
from receipt_ocr.recognition.seal.preprocess.red_enhancement import enhance_oriented_seal


@pytest.mark.parametrize("color", ["red", "blue"])
@pytest.mark.parametrize("width,rotations", [(1000, []), (250, [90, -12.361])])
def test_verified_grey_ink_survives_enhancement_after_native_isolation(
    tmp_path, color, width, rotations,
):
    # The first two patches use the real grey-red Tianjin regression pixels.
    # Enhancement must keep those strokes even after enlargement/rotation.
    raw = np.full((120, width, 3), 255, np.uint8)
    raw[20:45, 20:35] = (184, 188, 193)
    raw[20:45, 50:65] = (179, 178, 187)
    raw[70:90, 20:65] = (180, 180, 180)
    raw[70:90, 100:145] = (70, 70, 75)
    if color == "blue":
        raw = raw[:, :, ::-1].copy()
    source, working = tmp_path / "source.png", tmp_path / "color.png"
    cv2.imwrite(str(source), raw)
    region = SealRegion(0, 0, 1, 1, color, "", 0)
    save_color_isolated_seal(source, working, region)
    for index, angle in enumerate(rotations):
        rotated = tmp_path / f"rotated-{index}.png"
        rotate_stamp_image(working, rotated, angle)
        working = rotated
    trusted = cv2.imread(str(working))
    # A previous working image can contain arbitrary amplified remnants.
    # Such remnants must not become a source for the preserved-ink floor.
    contaminated = trusted.copy()
    contaminated[trusted[:, :, 1] == 255] = (20, 20, 255) if color == "red" else (255, 20, 20)
    cv2.imwrite(str(working), contaminated)
    destination = tmp_path / "enhanced.png"
    enhance_oriented_seal(source, region, working, destination,
                         {"rotation_steps": rotations})
    actual = cv2.imread(str(destination))
    grey_strokes = (trusted[:, :, 1] < 200)
    assert grey_strokes.any()
    assert np.all(actual[:, :, 1][grey_strokes] <= trusted[:, :, 1][grey_strokes])
    # Both isolated neutral patches stay white, despite the red/blue noise
    # deliberately inserted into the intermediate image.
    assert np.all(actual[trusted[:, :, 1] == 255, 1] > 230)


def test_original_chroma_controls_ink_even_when_old_image_contains_red_noise(tmp_path):
    source = tmp_path / "source.png"
    raw = np.full((200, 1000, 3), 255, np.uint8)
    raw[30:70, 100:150] = (45, 45, 45)  # Neutral form ink.
    raw[30:70, 300:350] = (220, 220, 245)  # Pale red glyph.
    raw[30:70, 500:550] = (70, 70, 75)  # Tiny red bias in dark print.
    raw[30:70, 700:750] = (50, 50, 180)  # Strong red glyph.
    cv2.imwrite(str(source), raw)
    old = np.full_like(raw, 255)
    old[30:70, :, :2] = 20  # Deliberately over-enhanced old noise.
    working = tmp_path / "old.png"
    cv2.imwrite(str(working), old)
    before = working.read_bytes()
    destination = tmp_path / "enhanced.png"
    enhance_oriented_red_seal(
        source, SealRegion(0, 0, 1, 1, "red", "", 0), working,
        destination, {"rotation_steps": []},
    )
    result = cv2.imread(str(destination))
    assert np.all(result[50, 120] == 255)
    assert 180 < result[50, 320, 0] < 220
    assert result[50, 520, 0] > 230
    assert result[50, 720, 0] < 80
    assert np.all(result[:, :, 2] == 255)
    assert working.read_bytes() == before


def test_coarse_and_fine_rotation_preserve_the_same_pixel_frame(tmp_path):
    source = tmp_path / "source.png"
    raw = np.full((240, 260, 3), 255, np.uint8)
    cv2.rectangle(raw, (170, 50), (200, 80), (60, 60, 240), -1)
    cv2.imwrite(str(source), raw)
    region = SealRegion(0, 0, 1, 1, "red", "", 0)
    working = tmp_path / "color.png"
    save_color_isolated_seal(source, working, region)
    for index, angle in enumerate([45, -12.361]):
        rotated = tmp_path / f"rotated-{index}.png"
        rotate_stamp_image(working, rotated, angle)
        working = rotated
    destination = tmp_path / "enhanced.png"
    enhance_oriented_red_seal(
        source, region, working, destination, {"rotation_steps": [45, -12.361]},
    )
    old = cv2.imread(str(working))
    new = cv2.imread(str(destination))
    assert new.shape == old.shape
    old_y, old_x = np.where(old[:, :, 1] < 150)
    new_y, new_x = np.where(new[:, :, 1] < 150)
    assert abs(old_x.mean() - new_x.mean()) < 1
    assert abs(old_y.mean() - new_y.mean()) < 1


def test_missing_mapping_or_other_colour_leaves_files_untouched(tmp_path):
    target = tmp_path / "enhanced.png"
    for color, orientation in [("blue", {"rotation_steps": []}), ("red", {})]:
        assert enhance_oriented_red_seal(
            tmp_path / "absent.png", SimpleNamespace(color=color),
            tmp_path / "absent-clean.png", target, orientation,
        ) is None
    assert not target.exists()


def test_mismatched_canvas_is_not_resized_to_fit(tmp_path):
    source, working, target = (tmp_path / name for name in ("source.png", "old.png", "new.png"))
    cv2.imwrite(str(source), np.full((100, 100, 3), 255, np.uint8))
    cv2.imwrite(str(working), np.full((90, 90, 3), 255, np.uint8))
    assert enhance_oriented_red_seal(
        source, SealRegion(0, 0, 1, 1, "red", "", 0),
        working, target, {"rotation_steps": []},
    ) is None
    assert not target.exists()
