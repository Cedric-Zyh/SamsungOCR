"""Trace visible stamp-border ink before masking the centre text."""
from __future__ import annotations

from receipt_ocr.runtime.image_processing import timed_image

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .colors import _ocr_color_mask
from .ellipse import _fit_ellipse_annulus
from .io import _read_image


@dataclass
class RingGuide:
    annulus: tuple
    source_shape: tuple[int, int]
    points: np.ndarray
    supported: np.ndarray
    map_x: np.ndarray
    map_y: np.ndarray

    def summary(self):
        return {
            "method": "observed_ellipse_border",
            "supported_fraction": round(float(self.supported.mean()), 4),
            "sample_count": len(self.points),
            "source_size": list(self.source_shape[::-1]),
            "unwrap_size": list(self.map_x.shape[::-1]),
        }


@timed_image('detection', '图形定位')
def trace_ellipse_border(source: str | Path, *, color: str = "red") -> RingGuide | None:
    """Use an ellipse only to locate a search strip, then follow actual ink.

    A periodic robust fit joins short gaps without following isolated glyphs.
    Nothing here uses OCR, the expected company name, or recognition scores.
    """
    image = _read_image(source)
    annulus = _fit_ellipse_annulus(_ocr_color_mask(image, color))
    if annulus is None:
        return None
    (cx, cy), (axis_a, axis_b), angle = annulus[0][2]
    a, b = axis_a / 2, axis_b / 2
    rotation = np.deg2rad(angle)
    co, si = np.cos(rotation), np.sin(rotation)
    theta = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    x = cx + a * np.cos(theta) * co - b * np.sin(theta) * si
    y = cy + a * np.cos(theta) * si + b * np.sin(theta) * co
    nx = np.cos(theta) / a * co - np.sin(theta) / b * si
    ny = np.cos(theta) / a * si + np.sin(theta) / b * co
    length = np.hypot(nx, ny)
    nx, ny = nx / length, ny / length
    reach = max(3, round(min(a, b) * .13))
    offsets = np.arange(-reach, reach + 1)
    map_x = x + offsets[:, None] * nx
    map_y = y + offsets[:, None] * ny
    blue, green, red = cv2.split(image.astype(np.float32))
    ink = red - np.maximum(blue, green) if color == "red" else blue - np.maximum(green, red)
    strip = cv2.remap(np.clip(ink, 0, 255), map_x.astype(np.float32), map_y.astype(np.float32),
                      cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    strip = cv2.GaussianBlur(strip, (5, 5), 0)
    peaks = np.argmax(strip, axis=0)
    strength = strip[peaks, np.arange(len(theta))]
    observed = offsets[peaks].astype(float)
    valid = strength > max(5, float(np.quantile(strength, .7)) * .15)
    if valid.mean() < .65:
        return None
    design = np.column_stack([np.ones(len(theta))] + [
        function(harmonic * theta)
        for harmonic in range(1, 9) for function in (np.cos, np.sin)
    ])
    base_weights = valid * np.minimum(1, strength / 30)
    weights = base_weights.copy()
    for _ in range(6):
        root = np.sqrt(weights)
        coefficients = np.linalg.lstsq(design * root[:, None], observed * root, rcond=None)[0]
        fitted = design @ coefficients
        residual = np.abs(observed - fitted)
        weights = base_weights * np.minimum(1, 3 / np.maximum(residual, 1e-6))
    supported = valid & (np.abs(observed - fitted) <= max(6, min(a, b) * .02))
    if supported.mean() < .65 or np.max(np.abs(fitted)) > reach:
        return None
    # Do not infer a long missing arc solely from the opposite half of a seal.
    longest_gap = current_gap = 0
    for present in np.tile(supported, 2):
        current_gap = 0 if present else current_gap + 1
        longest_gap = max(longest_gap, current_gap)
    if longest_gap > len(theta) * .25:
        return None
    points = np.column_stack((x + fitted * nx, y + fitted * ny))

    # Parameterise the measured ring in ellipse coordinates. This accounts
    # for the differing axes in one resampling step, without making a circle
    # image first. Natural radial/angular scale fixes the text aspect ratio.
    seam = int(np.argmax(points[:, 1]))
    ordered = np.roll(points, -seam, axis=0)
    width = max(1200, min(3600, round(np.pi * (a + b))))
    coordinate = np.linspace(0, len(points), width, endpoint=False)
    closed = np.vstack((ordered, ordered[0]))
    border_x = np.interp(coordinate, np.arange(len(closed)), closed[:, 0])
    border_y = np.interp(coordinate, np.arange(len(closed)), closed[:, 1])
    outer_fraction = .95
    inner_fraction = .45 if annulus[2] else annulus[1][0] / annulus[0][0] * 1.08
    inner_fraction = min(.90, inner_fraction)
    height = max(64, round(width * (outer_fraction - inner_fraction) / (2 * np.pi)))
    radial = np.linspace(outer_fraction, inner_fraction, height)[:, None]
    map_x = (cx + (border_x - cx) * radial).astype(np.float32)
    map_y = (cy + (border_y - cy) * radial).astype(np.float32)
    return RingGuide(annulus, image.shape[:2], points, supported, map_x, map_y)


def save_ring_border_overlay(source: str | Path, destination: str | Path, guide: RingGuide) -> Path:
    """Green follows measured ink; amber marks gaps reconstructed by the fit."""
    image = _read_image(source).copy()
    points = np.rint(guide.points).astype(np.int32)
    thickness = max(1, round(min(image.shape[:2]) / 450))
    for index in range(len(points)):
        following = (index + 1) % len(points)
        color = (0, 150, 0) if guide.supported[index] and guide.supported[following] else (0, 170, 230)
        cv2.line(image, tuple(points[index]), tuple(points[following]), color, thickness, cv2.LINE_AA)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("印章边框检测叠加图编码失败")
    encoded.tofile(str(destination))
    return destination
