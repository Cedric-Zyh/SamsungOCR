import numpy as np
from PIL import Image, ImageDraw

from receipt_ocr.date_crop_preparation import _save_positioned_outer_frame_clean


def clean(tmp_path, image):
    source, destination = tmp_path / 'source.png', tmp_path / 'clean.png'
    image.save(source)
    _save_positioned_outer_frame_clean(source, destination)
    with Image.open(destination) as opened:
        return np.asarray(opened.convert('RGB')).copy()


def test_crossing_strokes_preserved_and_bare_pale_rules_removed(tmp_path):
    image = Image.new('RGB', (450, 110), 'white')
    draw = ImageDraw.Draw(image)
    for y in (15, 75):
        draw.line((0, y - 2, 449, y - 2), fill=(230, 230, 230))
        draw.line((0, y, 449, y), fill='black', width=3)
        draw.line((0, y + 2, 449, y + 2), fill=(230, 230, 230))
    draw.line((100, 4, 100, 96), fill='black', width=3)
    draw.line((290, 3, 270, 99), fill='black', width=3)
    # A middle-of-cell horizontal digit stroke is not a frame.
    draw.line((140, 44, 200, 44), fill='black', width=3)
    original = np.asarray(image)
    result = clean(tmp_path, image)
    for y in (15, 75):
        assert (result[y-2:y+3, 25:70] == 255).all()
    for vertical in (True, False):
        # Only check the actual crossing footprint, not all the rule pixels.
        for y in (15, 75):
            x = 100 if vertical else round(290 - (y - 3) * 20 / 96)
            assert (result[y-2:y+3, x, :] == original[y-2:y+3, x, :]).all()
    assert (result[44, 140:201] == 0).all()


def test_no_rule_does_not_change_handwriting(tmp_path):
    image = Image.new('RGB', (450, 110), 'white')
    draw = ImageDraw.Draw(image)
    draw.line((180, 4, 165, 95), fill='black', width=3)
    draw.line((260, 35, 285, 55), fill='black', width=3)
    assert np.array_equal(clean(tmp_path, image), np.asarray(image))


def test_distant_marks_do_not_protect_a_bare_rule(tmp_path):
    image = Image.new('RGB', (450, 110), 'white')
    draw = ImageDraw.Draw(image)
    draw.line((0, 18, 449, 18), fill='black', width=3)
    draw.line((100, 4, 100, 12), fill='black', width=3)
    draw.line((150, 23, 150, 35), fill='black', width=3)
    result = clean(tmp_path, image)
    assert (result[17:20, 90:160] == 255).all()
