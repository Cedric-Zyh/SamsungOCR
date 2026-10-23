"""Independent normal-offset ring evidence; never a matching candidate."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import (
    _fit_ellipse_annulus, _ellipse_normal_maps, _save_ellipse_annulus_unwrapped,
)
from receipt_ocr.imaging.ring_border import trace_ellipse_border


def read_normal_alternative(directory, index, prefix, artifact, *, reader, provider):
    """Read the same stamp's masked pixels; selection follows after OCR."""
    evidence = {"status": "unavailable", "matching": False,
                "region_id": artifact.get("region_id"),
                "method": "ellipse_normals_alternative"}
    if not artifact.get("ring_input_url"):
        return evidence
    directory = Path(directory)
    source = directory / Path(artifact["ring_input_url"]).name
    border_source = directory / Path(artifact.get("ring_input_source_url")
                                     or artifact["ring_input_url"]).name
    image, intact = cv2.imread(str(source)), cv2.imread(str(border_source))
    if image is None or intact is None or image.shape != intact.shape:
        return evidence
    observed = trace_ellipse_border(border_source, color=artifact["color"])
    geometry = (observed.annulus if observed is not None else
                _fit_ellipse_annulus(_ocr_color_mask(intact, artifact["color"])))
    if geometry is None:
        return evidence
    mx, my = _ellipse_normal_maps(*geometry)
    guide = SimpleNamespace(annulus=geometry, source_shape=image.shape[:2],
                            map_x=mx, map_y=my)
    b, g, r = (channel.astype(float) for channel in cv2.split(image))
    dominance = r-np.maximum(b, g) if artifact["color"] == "red" else b-np.maximum(r, g)
    gray = np.rint(255-np.clip(dominance*255/160, 0, 255)).astype(np.uint8)
    path = directory / f"seal-{index}-ring-normal-alternative.png"
    if not _save_ellipse_annulus_unwrapped(
            gray, _ocr_color_mask(image, artifact["color"]), path,
            single_line=True, ring_guide=guide, proportional=True):
        return evidence
    evidence.update(image_url=f"{prefix.rstrip('/')}/{path.name}" if prefix else "",
                    source_url=artifact["ring_input_url"], provider=provider)
    reads = reader.read_line(path, provider=provider)
    evidence.update(status="completed" if reads else "empty",
                    reads=[{"text": row.text, "confidence": float(row.confidence)} for row in reads],
                    text="".join(row.text for row in reads),
                    confidence=min((float(row.confidence) for row in reads), default=0))
    return evidence
