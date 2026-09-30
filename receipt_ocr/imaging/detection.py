"""Seal region detection and fragment grouping."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from .contracts import SealRegion
from .io import _read_image
from .colors import _color_masks


def _remove_page_spanning_vertical_scan_lines(mask: np.ndarray) -> np.ndarray:
    """Remove thin scanner streaks that otherwise absorb a real stamp contour.

    Some scans contain one-pixel red/pink lines running nearly the full page.
    If such a line crosses a customer stamp, dilation turns both into a single
    page-height contour and the normal size guard correctly rejects it.  A
    long vertical morphological opening isolates only those page-spanning
    streaks; ordinary rectangular-stamp edges are far shorter than the
    kernel and therefore remain intact.
    """
    height, width = mask.shape[:2]
    line_height = max(51, int(height * 0.22))
    long_vertical = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, line_height)),
    )
    if not cv2.countNonZero(long_vertical):
        return mask
    # JPEG ringing can make a one-pixel scan line two or three pixels wide.
    long_vertical = cv2.dilate(
        long_vertical,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)),
    )
    return cv2.bitwise_and(mask, cv2.bitwise_not(long_vertical))


def detect_seal_regions(path: str | Path) -> list[SealRegion]:
    """Detect colored stamp regions without calling an external service."""
    image = _read_image(path)
    height, width = image.shape[:2]
    red, blue = _color_masks(image)
    red = _remove_page_spanning_vertical_scan_lines(red)
    blue = _remove_page_spanning_vertical_scan_lines(blue)
    regions: list[SealRegion] = []

    for color_name, mask in (("red", red), ("blue", blue)):
        scale = max(5, int(min(width, height) * 0.006))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (scale, scale))
        grouped = cv2.dilate(mask, kernel, iterations=2)
        grouped = cv2.morphologyEx(grouped, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            nw, nh = w / width, h / height
            # A faint scanner streak can connect otherwise valid closed stamp
            # outlines into a page-spanning contour. Recover only enclosed,
            # stamp-sized inner contours before applying the normal oversize
            # rejection. This remains color-mask evidence and cannot admit
            # neutral black form text.
            if nw > 0.90 or nh > 0.42:
                oversized = SealRegion(
                    x=x / width,
                    y=y / height,
                    width=nw,
                    height=nh,
                    color=color_name,
                    role="收货客户章",
                    pixel_ratio=0.0,
                )
                inner = _split_merged_seals_by_inner_contours(
                    image, mask, oversized
                )
                regions.extend(inner)
                # A pale rectangular customer stamp can share a thin scan
                # line with the shipping and customer circles, producing one
                # near-page-wide contour. Closed inner contours recover the
                # circles but not a broken rectangle. Add only the customer
                # side of a two-cluster fallback; later overlap deduplication
                # removes it when it merely duplicates an existing circle.
                if nw > 0.90 and nh < 0.32:
                    regions.extend(
                        item for item in _split_merged_seals(image, mask, oversized)
                        if item.role == "收货客户章"
                    )
                continue
            if nw < 0.07 or nh < 0.035 or nw > 0.995:
                continue
            # Large customer stamps often start in the product total row and
            # extend into the receiving footer. Filter by the candidate's
            # center instead of its top edge so those legitimate overlaps are
            # retained while header/table color noise remains excluded.
            if (y + h / 2) / height < 0.43:
                continue
            raw = mask[y : y + h, x : x + w]
            pixel_ratio = float(cv2.countNonZero(raw)) / float(max(1, w * h))
            if pixel_ratio < 0.012:
                continue
            # Two stamps on the same table row can be joined by faint colored
            # transfer into one almost page-wide contour. Keep only dense,
            # stamp-sized super-wide candidates so the splitter below can
            # separate them; ordinary page-wide color noise remains rejected.
            if nw > 0.62 and not (nw * nh > 0.07 and pixel_ratio < 0.18):
                continue
            pad_x = int(width * 0.012)
            pad_y = int(height * 0.008)
            x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
            x2, y2 = min(width, x + w + pad_x), min(height, y + h + pad_y)
            cx = (x1 + x2) / 2 / width
            cy = (y1 + y2) / 2 / height
            regions.append(
                SealRegion(
                    x=x1 / width,
                    y=y1 / height,
                    width=(x2 - x1) / width,
                    height=(y2 - y1) / height,
                    color=color_name,
                    role=classify_seal_role(cx, cy),
                    pixel_ratio=round(pixel_ratio, 4),
                )
            )

    # Contours from a single stamp can occasionally split. Keep larger overlapping box.
    kept = _merge_split_stamp_fragments(_dedupe_overlapping_regions(regions))
    expanded: list[SealRegion] = []
    for region in kept:
        if (
            region.width > 0.62
            or region.width * region.height > 0.085
            or (
                region.width * region.height > 0.07
                and region.pixel_ratio < 0.14
            )
        ):
            # A joined left/right pair can have its combined center just left
            # of 0.5 and therefore be provisionally classified as a shipping
            # stamp. Split first, then classify each cluster by its own center;
            # otherwise the real receiving stamp is silently discarded.
            mask = red if region.color == "red" else blue
            split = _split_merged_seals_by_inner_contours(image, mask, region)
            if len(split) < 2:
                split = _split_merged_seals(image, mask, region)
            expanded.extend(split or [region])
        else:
            expanded.append(region)
    return sorted(_merge_split_stamp_fragments(expanded), key=lambda r: (r.y, r.x))


def _dedupe_overlapping_regions(regions: list[SealRegion]) -> list[SealRegion]:
    """Merge duplicate contours while resolving conflicting scan colors safely."""
    ordered = sorted(regions, key=lambda r: r.width * r.height, reverse=True)
    kept: list[SealRegion] = []
    for candidate in ordered:
        overlap_index = next(
            (index for index, existing in enumerate(kept)
             if _overlap_ratio(candidate, existing) > 0.6),
            None,
        )
        if overlap_index is not None:
            existing = kept[overlap_index]
            # JPEG color noise may create a slightly larger, barely populated
            # box in the opposite color. For cross-color conflicts, keep the
            # region with substantially stronger ink density.
            if (
                candidate.color != existing.color
                and candidate.pixel_ratio > existing.pixel_ratio * 1.25
            ):
                kept[overlap_index] = candidate
            continue
        kept.append(candidate)
    return kept


def _merge_split_stamp_fragments(regions: list[SealRegion]) -> list[SealRegion]:
    """Join two vertically overlapping boxes that are halves of one round stamp.

    A weak horizontal band through a large circular stamp can make the color
    contour detector return its upper and lower halves separately.  The guard
    below is deliberately narrow: fragments must have the same ink color and
    role, nearly identical horizontal extent, substantial horizontal overlap,
    and a centre displacement characteristic of adjacent halves.  This avoids
    combining ordinary duplicated stamps elsewhere on the page.
    """
    pending = sorted(regions, key=lambda item: (item.y, item.x))
    merged: list[SealRegion] = []
    used: set[int] = set()
    for index, first in enumerate(pending):
        if index in used:
            continue
        best_index: int | None = None
        best_score = 0.0
        for other_index in range(index + 1, len(pending)):
            if other_index in used:
                continue
            second = pending[other_index]
            if first.color != second.color or first.role != second.role:
                continue
            width_ratio = min(first.width, second.width) / max(first.width, second.width)
            height_ratio = min(first.height, second.height) / max(first.height, second.height)
            horizontal = max(
                0.0,
                min(first.x + first.width, second.x + second.width)
                - max(first.x, second.x),
            ) / max(1e-9, min(first.width, second.width))
            center_dx = abs(
                (first.x + first.width / 2) - (second.x + second.width / 2)
            ) / max(first.width, second.width)
            center_dy = abs(
                (first.y + first.height / 2) - (second.y + second.height / 2)
            ) / max(first.height, second.height)
            vertical_overlap = max(
                0.0,
                min(first.y + first.height, second.y + second.height)
                - max(first.y, second.y),
            ) / max(1e-9, min(first.height, second.height))
            if not (
                0.09 <= first.width <= 0.24
                and 0.09 <= second.width <= 0.24
                and 0.065 <= first.height <= 0.15
                and 0.065 <= second.height <= 0.15
                and width_ratio >= 0.88
                and height_ratio >= 0.80
                and horizontal >= 0.82
                and center_dx <= 0.15
                and 0.48 <= center_dy <= 0.78
                and 0.20 <= vertical_overlap <= 0.55
            ):
                continue
            score = horizontal + width_ratio + height_ratio - center_dx
            if score > best_score:
                best_score = score
                best_index = other_index
        if best_index is None:
            merged.append(first)
            continue
        second = pending[best_index]
        used.add(best_index)
        left = min(first.x, second.x)
        top = min(first.y, second.y)
        right = max(first.x + first.width, second.x + second.width)
        bottom = max(first.y + first.height, second.y + second.height)
        # Large stamps close to the right page edge need extra horizontal room
        # for the company-name arc that triggered no connected contour.
        pad_x = 0.04 if right >= 0.90 else 0.018
        pad_y = 0.010
        left, top = max(0.0, left - pad_x), max(0.0, top - pad_y)
        right, bottom = min(1.0, right + pad_x), min(1.0, bottom + pad_y)
        merged.append(SealRegion(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
            color=first.color,
            role=classify_seal_role((left + right) / 2, (top + bottom) / 2),
            pixel_ratio=round(max(first.pixel_ratio, second.pixel_ratio), 4),
        ))
    return merged


def classify_seal_role(center_x: float, center_y: float) -> str:
    """Use both table column and vertical position for displaced customer stamps."""
    # Customer stamps are normally in the right-hand receiving column, but a
    # large round stamp may be placed below the table and drift far to the left.
    return "收货客户章" if center_x >= 0.5 or center_y >= 0.62 else "发货单位章"


def _overlap_ratio(a: SealRegion, b: SealRegion) -> float:
    x1, y1 = max(a.x, b.x), max(a.y, b.y)
    x2 = min(a.x + a.width, b.x + b.width)
    y2 = min(a.y + a.height, b.y + b.height)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    smaller = min(a.width * a.height, b.width * b.height)
    return intersection / smaller if smaller else 0.0


def _split_merged_seals(image: np.ndarray, mask: np.ndarray, region: SealRegion) -> list[SealRegion]:
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    local = mask[y1:y2, x1:x2]
    ys, xs = np.where(local > 0)
    if len(xs) < 1000:
        return []
    data = np.column_stack((xs / max(1, x2 - x1), ys / max(1, y2 - y1))).astype(np.float32)
    _, labels, centers = cv2.kmeans(
        data,
        2,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 0.01),
        8,
        cv2.KMEANS_PP_CENTERS,
    )
    if np.linalg.norm(centers[0] - centers[1]) < 0.28:
        return []
    side = int(min(x2 - x1, y2 - y1) * 0.67)
    output: list[SealRegion] = []
    for index, center in enumerate(centers):
        cx = x1 + int(center[0] * (x2 - x1))
        cy = y1 + int(center[1] * (y2 - y1))
        sx1, sy1 = max(0, cx - side // 2), max(0, cy - side // 2)
        sx2, sy2 = min(width, sx1 + side), min(height, sy1 + side)
        cluster_size = int(np.count_nonzero(labels.ravel() == index))
        ratio = cluster_size / max(1, (sx2 - sx1) * (sy2 - sy1))
        output.append(
            SealRegion(
                x=sx1 / width,
                y=sy1 / height,
                width=(sx2 - sx1) / width,
                height=(sy2 - sy1) / height,
                color=region.color,
                role=classify_seal_role(cx / width, cy / height),
                pixel_ratio=round(ratio, 4),
            )
        )
    return output


def _split_merged_seals_by_inner_contours(
    image: np.ndarray, mask: np.ndarray, region: SealRegion
) -> list[SealRegion]:
    """Recover individual oval/circular outlines inside one dense merged box.

    K-means works for two spatially separated stamps, but multiple overlapping
    oval stamps have interleaved ink and produce mixed clusters. Their outer
    rings usually remain distinct in the lightly closed raw color mask, so use
    those contours before falling back to K-means.
    """
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    local = mask[y1:y2, x1:x2]
    if local.size == 0:
        return []
    close_size = max(3, int(min(width, height) * 0.0025) | 1)
    closed = cv2.morphologyEx(
        local,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size)),
        iterations=1,
    )
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[SealRegion] = []
    for contour in contours:
        cx, cy, cw, ch = cv2.boundingRect(contour)
        nw, nh = cw / width, ch / height
        contour_ratio = cv2.contourArea(contour) / max(1.0, cw * ch)
        # Reviewed circular/oval customer stamps occupy roughly 12–32% of the
        # page width and 7–22% of its height. Require a real enclosed contour
        # so text strokes do not become independent stamp regions.
        if not (0.12 <= nw <= 0.32 and 0.07 <= nh <= 0.22):
            continue
        if contour_ratio < 0.10:
            continue
        gx1, gy1 = x1 + cx, y1 + cy
        gx2, gy2 = gx1 + cw, gy1 + ch
        pad_x, pad_y = int(width * 0.010), int(height * 0.007)
        gx1, gy1 = max(0, gx1 - pad_x), max(0, gy1 - pad_y)
        gx2, gy2 = min(width, gx2 + pad_x), min(height, gy2 + pad_y)
        center_x = (gx1 + gx2) / 2 / width
        center_y = (gy1 + gy2) / 2 / height
        raw = mask[gy1:gy2, gx1:gx2]
        pixel_ratio = cv2.countNonZero(raw) / max(1, raw.size)
        candidates.append(SealRegion(
            x=gx1 / width,
            y=gy1 / height,
            width=(gx2 - gx1) / width,
            height=(gy2 - gy1) / height,
            color=region.color,
            role=classify_seal_role(center_x, center_y),
            pixel_ratio=round(float(pixel_ratio), 4),
        ))
    return sorted(_dedupe_overlapping_regions(candidates), key=lambda item: (item.y, item.x))
