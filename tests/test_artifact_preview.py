from io import BytesIO

import numpy as np
from flask import Flask
from PIL import Image

from receipt_ocr.web.artifact_preview import send_rgb_artifact_preview


def test_grayscale_preview_preserves_pixels_source_and_conditional_cache(tmp_path):
    pixels = np.array([[0, 64, 128], [192, 240, 255]], dtype=np.uint8)
    path = tmp_path / "ring.png"
    Image.fromarray(pixels).save(path)
    original = path.read_bytes()
    app = Flask(__name__)
    app.add_url_rule("/<path:name>", view_func=lambda name: send_rgb_artifact_preview(tmp_path, name))
    client = app.test_client()
    response = client.get("/ring.png")
    preview = Image.open(BytesIO(response.data))
    assert response.status_code == 200
    assert preview.mode == "RGB"
    assert preview.size == (3, 2)
    assert np.array_equal(np.array(preview), np.repeat(pixels[:, :, None], 3, axis=2))
    assert path.read_bytes() == original
    assert client.get("/ring.png", headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    assert client.get("/../outside.png").status_code == 404
    assert client.get("/missing.png").status_code == 404


def test_color_artifact_is_served_unchanged(tmp_path):
    path = tmp_path / "seal.png"
    Image.new("RGB", (10, 10), (220, 40, 80)).save(path)
    app = Flask(__name__)
    app.add_url_rule("/<path:name>", view_func=lambda name: send_rgb_artifact_preview(tmp_path, name))
    assert app.test_client().get("/seal.png").data == path.read_bytes()
