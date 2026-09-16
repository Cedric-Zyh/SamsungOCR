"""Image features, color masks and cached visual comparisons."""

from __future__ import annotations

import re
import threading
from pathlib import Path
import cv2
import numpy as np
from .seal_reference_constants import (
    COLOR_MASK_CANVAS_SIZE,
    COLOR_MASK_ROTATIONS,
    TRIMMED_CHROMATIC_QUANTILE,
)


class SealReferenceGeometry:
    """Cached image representations shared by visual-reference comparisons."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self._descriptor_cache: dict[str, tuple[list, np.ndarray | None, tuple[int, int]]] = {}
        self._chromatic_descriptor_cache: dict[
            str, tuple[list, np.ndarray | None, tuple[int, int]]
        ] = {}
        self._trimmed_chromatic_descriptor_cache: dict[
            str, tuple[list, np.ndarray | None, tuple[int, int]]
        ] = {}
        self._color_mask_cache: dict[str, np.ndarray | None] = {}
        self._lock = threading.RLock()
        self._sift = cv2.SIFT_create(
            nfeatures=1200,
            contrastThreshold=0.02,
            edgeThreshold=12,
        ) if hasattr(cv2, "SIFT_create") else None
        self._matcher = cv2.BFMatcher()

    @property
    def enabled(self) -> bool:
        return self._sift is not None

    def _artifact_path(self, url: str) -> Path | None:
        match = re.fullmatch(r"/files/artifacts/(.+)", url)
        if not match:
            return None
        candidate = (self.artifact_root / match.group(1)).resolve()
        try:
            candidate.relative_to(self.artifact_root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _compare(self, candidate: Path, reference: Path) -> dict:
        with self._lock:
            candidate_values = self._descriptors(candidate)
            reference_values = self._descriptors(reference)
        return self._compare_descriptor_values(
            candidate_values, reference_values
        )

    def _compare_chromatic_crop(
        self, candidate: Path, reference: Path
    ) -> dict:
        with self._lock:
            candidate_values = self._chromatic_descriptors(candidate)
            reference_values = self._chromatic_descriptors(reference)
        return self._compare_descriptor_values(
            candidate_values, reference_values
        )

    def _compare_trimmed_chromatic_crop(
        self, candidate: Path, reference: Path
    ) -> dict:
        with self._lock:
            candidate_values = self._trimmed_chromatic_descriptors(candidate)
            reference_values = self._trimmed_chromatic_descriptors(reference)
        return self._compare_descriptor_values(
            candidate_values, reference_values
        )

    def _compare_descriptor_values(
        self,
        candidate_values: tuple[
            list, np.ndarray | None, tuple[int, int]
        ],
        reference_values: tuple[
            list, np.ndarray | None, tuple[int, int]
        ],
    ) -> dict:
        candidate_keypoints, candidate_descriptors, candidate_shape = (
            candidate_values
        )
        reference_keypoints, reference_descriptors, reference_shape = (
            reference_values
        )
        with self._lock:
            if (
                candidate_descriptors is None
                or reference_descriptors is None
                or len(candidate_keypoints) < 8
                or len(reference_keypoints) < 8
            ):
                return _empty_metrics()
            pairs = self._matcher.knnMatch(
                candidate_descriptors, reference_descriptors, k=2
            )
        good = [
            first for first, second in pairs
            if first.distance < 0.72 * second.distance
        ]
        if len(good) < 6:
            return {**_empty_metrics(), "good_matches": len(good)}
        candidate_points = np.float32([
            candidate_keypoints[item.queryIdx].pt for item in good
        ])
        reference_points = np.float32([
            reference_keypoints[item.trainIdx].pt for item in good
        ])
        _homography, mask = cv2.findHomography(
            candidate_points.reshape(-1, 1, 2),
            reference_points.reshape(-1, 1, 2),
            cv2.RANSAC,
            5.0,
        )
        if mask is None:
            return {**_empty_metrics(), "good_matches": len(good)}
        inlier_mask = mask.ravel().astype(bool)
        inliers = int(inlier_mask.sum())
        return {
            "good_matches": len(good),
            "homography_inliers": inliers,
            "inlier_ratio": round(inliers / max(1, len(good)), 4),
            "candidate_coverage": round(
                _point_coverage(candidate_points[inlier_mask], candidate_shape), 4
            ),
            "reference_coverage": round(
                _point_coverage(reference_points[inlier_mask], reference_shape), 4
            ),
        }

    def _compare_color_mask(self, candidate: Path, reference: Path) -> dict:
        return _color_mask_similarity(
            self._color_mask(candidate), self._color_mask(reference)
        )

    def _write_chromatic_crop_artifact(
        self, path: Path, source_url: str
    ) -> str:
        """Persist the exact robust crop used by the alternate SIFT route."""
        crop = _chromatic_crop_image(path)
        if crop is None:
            return ""
        match = re.fullmatch(r"/files/artifacts/(.+)", source_url)
        if not match:
            return ""
        source_name = path.name
        if source_name.endswith("-color-isolated.png"):
            output_name = source_name.replace(
                "-color-isolated.png", "-chromatic-crop.png"
            )
        else:
            output_name = f"{path.stem}-chromatic-crop.png"
        output = path.with_name(output_name)
        if not output.is_file():
            encoded, payload = cv2.imencode(".png", crop)
            if not encoded:
                return ""
            payload.tofile(str(output))
        relative = output.relative_to(self.artifact_root).as_posix()
        return f"/files/artifacts/{relative}"

    def _write_trimmed_chromatic_crop_artifact(
        self, path: Path, source_url: str
    ) -> str:
        """Persist the sparse-noise-trimmed crop used by the narrow route."""
        crop = _trimmed_chromatic_crop_image(path)
        if crop is None:
            return ""
        match = re.fullmatch(r"/files/artifacts/(.+)", source_url)
        if not match:
            return ""
        source_name = path.name
        if source_name.endswith("-color-isolated.png"):
            output_name = source_name.replace(
                "-color-isolated.png", "-chromatic-trimmed-crop.png"
            )
        else:
            output_name = f"{path.stem}-chromatic-trimmed-crop.png"
        output = path.with_name(output_name)
        if not output.is_file():
            encoded, payload = cv2.imencode(".png", crop)
            if not encoded:
                return ""
            payload.tofile(str(output))
        relative = output.relative_to(self.artifact_root).as_posix()
        return f"/files/artifacts/{relative}"

    def _color_mask(self, path: Path) -> np.ndarray | None:
        key = str(path)
        if key not in self._color_mask_cache:
            self._color_mask_cache[key] = _normalized_color_ink(path)
        return self._color_mask_cache[key]

    def _descriptors(
        self, path: Path
    ) -> tuple[list, np.ndarray | None, tuple[int, int]]:
        key = str(path)
        cached = self._descriptor_cache.get(key)
        if cached is not None:
            return cached
        image = cv2.imdecode(
            np.fromfile(key, dtype=np.uint8), cv2.IMREAD_GRAYSCALE
        )
        if image is None:
            value = ([], None, (1, 1))
            self._descriptor_cache[key] = value
            return value
        ys, xs = np.where(image < 245)
        if len(xs) > 30:
            pad = 8
            x1, x2 = max(0, int(xs.min()) - pad), min(
                image.shape[1], int(xs.max()) + pad + 1
            )
            y1, y2 = max(0, int(ys.min()) - pad), min(
                image.shape[0], int(ys.max()) + pad + 1
            )
            image = image[y1:y2, x1:x2]
        scale = 800 / max(1, max(image.shape))
        if scale < 1.0 or scale > 1.2:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=(
                    cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                ),
            )
        keypoints, descriptors = self._sift.detectAndCompute(image, None)
        value = (keypoints or [], descriptors, image.shape[:2])
        self._descriptor_cache[key] = value
        return value

    def _chromatic_descriptors(
        self, path: Path
    ) -> tuple[list, np.ndarray | None, tuple[int, int]]:
        """Crop by colored ink, then retain grayscale texture for SIFT."""
        key = str(path)
        cached = self._chromatic_descriptor_cache.get(key)
        if cached is not None:
            return cached
        color = _chromatic_crop_image(path)
        if color is None:
            value = ([], None, (1, 1))
            self._chromatic_descriptor_cache[key] = value
            return value
        image = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
        scale = 800 / max(1, max(image.shape))
        if scale < 1.0 or scale > 1.2:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=(
                    cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                ),
            )
        keypoints, descriptors = self._sift.detectAndCompute(image, None)
        value = (keypoints or [], descriptors, image.shape[:2])
        self._chromatic_descriptor_cache[key] = value
        return value

    def _trimmed_chromatic_descriptors(
        self, path: Path
    ) -> tuple[list, np.ndarray | None, tuple[int, int]]:
        """Trim sparse colored outliers, then retain grayscale SIFT texture."""
        key = str(path)
        cached = self._trimmed_chromatic_descriptor_cache.get(key)
        if cached is not None:
            return cached
        color = _trimmed_chromatic_crop_image(path)
        if color is None:
            value = ([], None, (1, 1))
            self._trimmed_chromatic_descriptor_cache[key] = value
            return value
        image = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
        scale = 800 / max(1, max(image.shape))
        if scale < 1.0 or scale > 1.2:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=(
                    cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                ),
            )
        keypoints, descriptors = self._sift.detectAndCompute(image, None)
        value = (keypoints or [], descriptors, image.shape[:2])
        self._trimmed_chromatic_descriptor_cache[key] = value
        return value

def _empty_metrics() -> dict:
    return {
        "good_matches": 0,
        "homography_inliers": 0,
        "inlier_ratio": 0.0,
        "candidate_coverage": 0.0,
        "reference_coverage": 0.0,
    }

def _chromatic_crop_image(path: Path) -> np.ndarray | None:
    """Crop to chromatic ink while preserving original color/gray texture."""
    image = cv2.imdecode(
        np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return None
    blue, green, red = cv2.split(image)
    high = np.maximum(np.maximum(red, green), blue).astype(np.int16)
    low = np.minimum(np.minimum(red, green), blue).astype(np.int16)
    chromatic = ((high - low) >= 22) & (low <= 235)
    ys, xs = np.where(chromatic)
    if len(xs) < 180:
        return image
    pad = 8
    x1 = max(0, int(xs.min()) - pad)
    x2 = min(image.shape[1], int(xs.max()) + pad + 1)
    y1 = max(0, int(ys.min()) - pad)
    y2 = min(image.shape[0], int(ys.max()) + pad + 1)
    return image[y1:y2, x1:x2]

def _trimmed_chromatic_crop_image(path: Path) -> np.ndarray | None:
    """Crop colored ink after discarding sparse per-axis scanner outliers."""
    image = cv2.imdecode(
        np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return None
    blue, green, red = cv2.split(image)
    high = np.maximum(np.maximum(red, green), blue).astype(np.int16)
    low = np.minimum(np.minimum(red, green), blue).astype(np.int16)
    ys, xs = np.where(((high - low) >= 22) & (low <= 235))
    if len(xs) < 180:
        return image
    lower = TRIMMED_CHROMATIC_QUANTILE
    upper = 1.0 - lower
    x1_value, x2_value = np.quantile(xs, (lower, upper))
    y1_value, y2_value = np.quantile(ys, (lower, upper))
    pad = 8
    x1 = max(0, int(x1_value) - pad)
    x2 = min(image.shape[1], int(x2_value) + pad + 1)
    y1 = max(0, int(y1_value) - pad)
    y2 = min(image.shape[0], int(y2_value) + pad + 1)
    return image[y1:y2, x1:x2]

def _normalized_color_ink(path: Path) -> np.ndarray | None:
    """Center chromatic stamp ink and discard black form/handwriting lines."""
    image = cv2.imdecode(
        np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return None
    blue, green, red = cv2.split(image)
    high = np.maximum(np.maximum(red, green), blue).astype(np.int16)
    low = np.minimum(np.minimum(red, green), blue).astype(np.int16)
    mask = (((high - low) >= 22) & (low <= 235)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)
    )
    ys, xs = np.where(mask > 0)
    if len(xs) < 180:
        return None
    pad = 4
    x1 = max(0, int(xs.min()) - pad)
    x2 = min(mask.shape[1], int(xs.max()) + pad + 1)
    y1 = max(0, int(ys.min()) - pad)
    y2 = min(mask.shape[0], int(ys.max()) + pad + 1)
    crop = mask[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    target = COLOR_MASK_CANVAS_SIZE - 32
    scale = target / max(crop.shape)
    resized = cv2.resize(
        crop,
        (
            max(1, round(crop.shape[1] * scale)),
            max(1, round(crop.shape[0] * scale)),
        ),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    canvas = np.zeros(
        (COLOR_MASK_CANVAS_SIZE, COLOR_MASK_CANVAS_SIZE), np.uint8
    )
    top = (COLOR_MASK_CANVAS_SIZE - resized.shape[0]) // 2
    left = (COLOR_MASK_CANVAS_SIZE - resized.shape[1]) // 2
    canvas[
        top:top + resized.shape[0], left:left + resized.shape[1]
    ] = resized
    return canvas

def _shift_mask(image: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return cv2.warpAffine(
        image,
        np.float32([[1, 0, dx], [0, 1, dy]]),
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )

def _color_mask_similarity(
    candidate_mask: np.ndarray | None,
    reference_mask: np.ndarray | None,
) -> dict:
    if candidate_mask is None or reference_mask is None:
        return {
            "color_mask_score": 0.0,
            "color_mask_correlation": 0.0,
            "color_mask_dice": 0.0,
            "color_mask_angle": 0,
            "color_mask_dx": 0,
            "color_mask_dy": 0,
        }
    size = COLOR_MASK_CANVAS_SIZE
    yy, xx = np.ogrid[:size, :size]
    center = (size - 1) / 2
    radius = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    # The outer circle is common to unrelated stamps. Retain identifying ring
    # text, center star/logo and internal type line, but suppress the border.
    focus = (radius <= size * 0.43).astype(np.uint8)
    reference_focus = cv2.GaussianBlur(
        (reference_mask * focus).astype(np.float32) / 255.0,
        (5, 5),
        0,
    )
    padded = cv2.copyMakeBorder(
        reference_focus, 8, 8, 8, 8, cv2.BORDER_CONSTANT
    )
    best = {
        "color_mask_score": 0.0,
        "color_mask_correlation": 0.0,
        "color_mask_dice": 0.0,
        "color_mask_angle": 0,
        "color_mask_dx": 0,
        "color_mask_dy": 0,
    }
    for angle in COLOR_MASK_ROTATIONS:
        rotation = cv2.getRotationMatrix2D((center, center), angle, 1.0)
        rotated = cv2.warpAffine(
            candidate_mask,
            rotation,
            (size, size),
            flags=cv2.INTER_NEAREST,
            borderValue=0,
        )
        rotated_focus = cv2.GaussianBlur(
            (rotated * focus).astype(np.float32) / 255.0,
            (5, 5),
            0,
        )
        response = cv2.matchTemplate(
            padded, rotated_focus, cv2.TM_CCOEFF_NORMED
        )
        _minimum, correlation, _minimum_location, location = cv2.minMaxLoc(
            response
        )
        dx, dy = int(location[0] - 8), int(location[1] - 8)
        aligned = _shift_mask(rotated, dx, dy)
        left = (aligned > 64) & (focus > 0)
        right = (reference_mask > 64) & (focus > 0)
        intersection = int(np.logical_and(left, right).sum())
        dice = 2 * intersection / max(
            1, int(left.sum()) + int(right.sum())
        )
        score = max(0.0, 0.72 * float(correlation) + 0.28 * dice)
        if score > best["color_mask_score"]:
            best = {
                "color_mask_score": round(score, 4),
                "color_mask_correlation": round(float(correlation), 4),
                "color_mask_dice": round(float(dice), 4),
                "color_mask_angle": angle,
                "color_mask_dx": dx,
                "color_mask_dy": dy,
            }
    return best

def _point_coverage(points: np.ndarray, shape: tuple[int, int]) -> float:
    if len(points) < 2:
        return 0.0
    _x, _y, width, height = cv2.boundingRect(np.float32(points))
    return (width * height) / max(1, shape[0] * shape[1])
