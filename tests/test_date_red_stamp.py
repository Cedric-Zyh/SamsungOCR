import cv2
import numpy as np

from receipt_ocr.imaging.date import _remove_date_red_stamp, save_receipt_date_crop
from receipt_ocr.recognition.date.preprocess.crops import _save_date_line_crop
from receipt_ocr.recognition.date.preprocess.frame import _save_positioned_outer_frame_clean


def test_dense_red_is_removed_without_erasing_dark_crossing():
    image = np.full((40, 100, 3), 255, dtype=np.uint8)
    # This saturated stamp has low luminance and survived the old >=140 gate.
    image[5:35, 10:90] = (35, 20, 230)
    # Red-tinted black stroke across the stamp, with a small darker segment.
    image[10:30, 48:52] = (18, 12, 90)
    image[18:22, 48:52] = (5, 5, 35)
    before = image.copy()
    clean = _remove_date_red_stamp(image)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    assert np.all(clean[5:35, 10:48] == 255)
    assert np.array_equal(clean[10:30, 48:52], gray[10:30, 48:52])
    assert np.array_equal(image, before)


def test_date_line_and_frame_use_cleaned_pixels(tmp_path):
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    image[468:508, 760:995] = (35, 20, 230)
    # Interior stroke survives both color removal and subsequent frame cleanup.
    image[482:500, 880:884] = (18, 12, 90)
    source = tmp_path / "source.png"
    raw = tmp_path / "raw.jpg"
    clean = tmp_path / "color.png"
    line = tmp_path / "line.png"
    frame = tmp_path / "frame.png"
    cv2.imwrite(str(source), image)
    save_receipt_date_crop(
        source, tmp_path / "table.png", 0.4,
        raw_destination=raw, color_clean_destination=clean,
    )
    _save_date_line_crop(clean, line, tight=True)
    _save_positioned_outer_frame_clean(line, frame)

    expected = int(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)[490, 881])
    for path in (clean, line, frame):
        result = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        assert np.count_nonzero(result == expected) == 18 * 4
        assert np.count_nonzero(result == 255) == result.size - 18 * 4
    original = cv2.imread(str(raw))
    assert np.any(original[:, :, 2].astype(int) - original[:, :, 1] > 100)


def test_internal_date_ocr_input_keeps_long_vertical_digit_strokes(tmp_path):
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    image[474:504, 800:804] = 0
    image[474:504, 960:964] = 0
    source, output = tmp_path / 'source.png', tmp_path / 'table.png'
    cv2.imwrite(str(source), image)
    save_receipt_date_crop(source, output, 0.4)
    result = cv2.imread(str(output), cv2.IMREAD_GRAYSCALE)
    # Both tall 1-like strokes must survive the internal table-clean pass.
    assert np.count_nonzero(result[:, :750] < 100) > 1000
    assert np.count_nonzero(result[:, 750:] < 100) > 1000
