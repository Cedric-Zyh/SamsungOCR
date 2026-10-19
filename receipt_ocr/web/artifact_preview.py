"""Browser-compatible evidence previews, without altering the OCR source."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from flask import abort, send_file, send_from_directory
from PIL import Image
from werkzeug.utils import safe_join


def send_rgb_artifact_preview(directory, name):
    path = safe_join(str(directory), name)
    if path is None or not Path(path).is_file():
        abort(404)
    if Path(path).suffix.lower() != ".png":
        return send_from_directory(directory, name)
    with Image.open(path) as source:
        if source.mode not in {"1", "L", "LA"}:
            return send_from_directory(directory, name)
        # Some desktop WebViews render single-channel PNGs as white. Replicate
        # the grey value in RGB; no resizing, thresholding or OCR changes.
        image = source.convert("RGBA" if source.mode == "LA" else "RGB")
        output = BytesIO()
        image.save(output, format="PNG")
    content = output.getvalue()
    output.seek(0)
    return send_file(output, mimetype="image/png", download_name=Path(name).name,
                     etag=sha256(content).hexdigest(), conditional=True, max_age=0)
