from PIL import Image, ImageDraw
import pytest

from receipt_ocr.date_evidence import _save_date_line_crop


@pytest.mark.parametrize("tight,stroke_top", [(True, 55), (False, 70)])
def test_date_line_preserves_handwriting_above_table_rule(tmp_path, tight, stroke_top):
    image = Image.new("RGB", (685, 238), "white")
    draw = ImageDraw.Draw(image)
    # A tall handwritten digit crosses the printed cell boundary, as on
    # 7281100561. Red ink must not hide the black ascender in the source crop.
    draw.line((0, 85, 684, 85), fill="black", width=2)
    draw.line((380, stroke_top, 380, 135), fill="black", width=3)
    source, destination = tmp_path / "source.png", tmp_path / "line.png"
    image.save(source)
    x, y, w, h = _save_date_line_crop(source, destination, tight=tight)
    left, top = round(x * 685), round(y * 238)
    with Image.open(destination) as cropped:
        assert top < stroke_top
        assert cropped.getpixel((380 - left, stroke_top - top)) == (0, 0, 0)
        assert cropped.getpixel((380 - left, 135 - top)) == (0, 0, 0)
        assert cropped.size == (round((x + w) * 685) - left,
                                round((y + h) * 238) - top)


def test_lower_date_line_keeps_top_edge(tmp_path):
    image = Image.new("RGB", (600, 200), "white")
    image.putpixel((400, 0), (0, 0, 0))
    source, destination = tmp_path / "source.png", tmp_path / "line.png"
    image.save(source)
    x, y, _, _ = _save_date_line_crop(source, destination, tight=False, lower=True)
    assert y == 0
    with Image.open(destination) as cropped:
        assert cropped.getpixel((400 - round(x * 600), 0)) == (0, 0, 0)
