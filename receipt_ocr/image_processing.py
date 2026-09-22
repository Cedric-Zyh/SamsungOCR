from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from .execution import timed


class DateCropOutOfRange(ValueError):
    """A date-crop window lies entirely outside the image.

    The date crops are offsets from the signature-requirement anchor.  On a
    layout whose signature row already sits near the page bottom, an offset
    lands past the edge and the slice comes back empty.  ``cv2.imencode``
    asserts on an empty image, which used to abort the whole date stage over
    an audit region that simply had no room to exist.
    """


@dataclass(frozen=True)
class SealRegion:
    x: float
    y: float
    width: float
    height: float
    color: str
    role: str
    pixel_ratio: float
    qingtong_cls: str = ""

    def to_dict(self) -> dict:
        value = asdict(self)
        if not self.qingtong_cls:
            value.pop("qingtong_cls")
        return value


def _read_image(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法解码图片: {path}")
    return image


@timed('qr_decode')
def decode_qr(path: str | Path) -> str:
    image = _read_image(path)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    return text or ""


def _color_masks(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red_a = cv2.inRange(hsv, (0, 55, 70), (16, 255, 255))
    red_b = cv2.inRange(hsv, (164, 45, 65), (179, 255, 255))
    red = cv2.bitwise_or(red_a, red_b)
    blue = cv2.inRange(hsv, (82, 45, 55), (140, 255, 255))
    # Very pale stamps can have too little saturation for HSV even though the
    # ink channel remains slightly dominant. Channel-difference masks retain
    # those strokes without admitting neutral black/gray form text.
    b, g, r = (channel.astype(np.int16) for channel in cv2.split(image))
    pale_red = np.where(
        (r >= 105) & (r - np.maximum(g, b) >= 5), 255, 0
    ).astype(np.uint8)
    red = cv2.bitwise_or(red, pale_red)
    return red, blue


def _ocr_color_mask(crop: np.ndarray, color: str) -> np.ndarray:
    """Return stamp ink while rejecting neutral form-line fringes.

    ``_color_masks`` is deliberately permissive because it is used to find a
    faint stamp on a whole page.  That permissiveness is harmful after a
    customer-stamp crop: antialiased black table rules can acquire a small
    red-channel bias and survive as horizontal fragments in the OCR image.
    The OCR derivative therefore uses a stricter chroma gate and removes only
    long, nearly-horizontal *low-saturation* runs.  Red/blue pixels are never
    removed by the line pass, so a coloured character crossing a form rule is
    preserved.
    """
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    if color == "red":
        hue_match = (hue <= 16) | (hue >= 164)
    else:
        hue_match = (hue >= 82) & (hue <= 140)
    # Keep the gate below the saturation of faint scanned red lettering.  The
    # neutral-line pass below removes long low-saturation table rules, so this
    # lower value does not re-admit the black form rows that the colour-safe
    # derivative is meant to exclude.
    mask = (
        hue_match
        & (saturation >= 45)
        & (value >= 50)
    ).astype(np.uint8) * 255

    if mask.size:
        # Detect only neutral dark lines.  The long horizontal opening avoids
        # treating individual Chinese strokes as a form line; the small
        # dilation clears antialiased edge pixels without touching coloured
        # ink because the subtraction is restricted to ``mask``.
        neutral = ((saturation < 55) & (value < 215)).astype(np.uint8) * 255
        kernel_width = max(24, mask.shape[1] // 14)
        horizontal = cv2.morphologyEx(
            neutral,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1)),
        )
        horizontal = cv2.dilate(
            horizontal,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)),
        )
        mask = cv2.bitwise_and(mask, cv2.bitwise_not(horizontal))
    return mask


def seal_region_is_rectangular(
    source: str | Path,
    region: SealRegion,
) -> bool:
    """Distinguish a linear rectangle from a wide oval stamp.

    Aspect ratio alone misclassifies common 2:1 oval customer stamps.  A
    rectangular border occupies almost all of its rotated bounding box,
    whereas an oval occupies roughly pi/4.  Use the colored border geometry
    and retain the historical aspect-ratio fallback only when no usable
    contour survives the chroma gate.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    ratio = max(crop.shape[:2]) / max(1, min(crop.shape[:2]))
    if ratio < 1.30:
        return False
    if crop.size == 0:
        return ratio >= 1.45
    mask = _ocr_color_mask(crop, region.color)
    kernel_size = max(3, round(min(mask.shape[:2]) * 0.018))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    grouped = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(
        grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        # QingTong is useful as a last-resort hint when the local colour mask
        # is too faint to recover a contour.  It must not override a usable
        # local geometry result: the API occasionally calls an oval a
        # rectangle (or a circle).
        return str(region.qingtong_cls).strip().lower() in {
            "rectangle", "rect", "矩形"
        } or ratio >= 1.45
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < mask.size * 0.025:
        return str(region.qingtong_cls).strip().lower() in {
            "rectangle", "rect", "矩形"
        } or ratio >= 1.45
    rect = cv2.minAreaRect(contour)
    rw, rh = rect[1]
    extent = cv2.contourArea(contour) / max(1.0, rw * rh)
    perimeter = cv2.arcLength(contour, True)
    vertices = len(cv2.approxPolyDP(contour, perimeter * 0.025, True))
    return bool(extent >= 0.84 and vertices <= 8)


def seal_region_is_elliptical(
    source: str | Path,
    region: SealRegion,
) -> bool:
    """Classify a non-rectangular stamp as an oval from its own pixels.

    QingTong's ``cls`` is retained as a fallback for an extremely faint crop,
    but a usable local colour mask wins.  The decision is intentionally based
    on the ink bounding box rather than the API label: a horizontal oval has a
    stable long/short-axis ratio even when its inner text is fragmented.
    """
    if seal_region_is_rectangular(source, region):
        return False
    try:
        image = _read_image(source)
        height, width = image.shape[:2]
        x1, y1 = int(region.x * width), int(region.y * height)
        x2 = int((region.x + region.width) * width)
        y2 = int((region.y + region.height) * height)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            raise ValueError("椭圆章区域为空")
        mask = _ocr_color_mask(crop, region.color)
        points = cv2.findNonZero(mask)
        if points is not None:
            bx, by, bw, bh = cv2.boundingRect(points)
            axis_ratio = max(bw, bh) / max(1.0, min(bw, bh))
            if axis_ratio >= 1.18:
                return True
            # A contour fit is more stable than the crop box when the API
            # added generous margins around a nearly circular stamp.
            if len(points) >= 5:
                ellipse = cv2.fitEllipse(points)
                major, minor = sorted(ellipse[1], reverse=True)
                if major / max(1.0, minor) >= 1.18:
                    return True
        label = str(region.qingtong_cls).strip().lower()
        return label in {"ellipse", "oval", "椭圆"}
    except (OSError, ValueError, TypeError, cv2.error):
        label = str(region.qingtong_cls).strip().lower()
        return label in {"ellipse", "oval", "椭圆"}


def seal_region_shape(source: str | Path, region: SealRegion) -> str:
    """Return the locally validated shape: ``rectangle``, ``ellipse`` or ``circle``."""
    if seal_region_is_rectangular(source, region):
        return "rectangle"
    if seal_region_is_elliptical(source, region):
        return "ellipse"
    return "circle"


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


def extract_region_text(
    observations: list,
    region: SealRegion,
    *,
    padding: float = 0.015,
) -> str:
    texts: list[tuple[float, float, str]] = []
    x1, y1 = region.x - padding, region.y - padding
    x2 = region.x + region.width + padding
    y2 = region.y + region.height + padding
    for row in observations:
        cx, cy = row.x + row.width / 2, row.y + row.height / 2
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            texts.append((row.y, row.x, row.text))
    return "".join(text for _, _, text in sorted(texts))


@timed('preview_generation')
def annotate_image(
    source: str | Path,
    destination: str | Path,
    seal_regions: list[SealRegion],
    date_box: tuple[float, float, float, float] | None = None,
) -> None:
    image = _read_image(source)
    height, width = image.shape[:2]
    for region in seal_regions:
        x1, y1 = int(region.x * width), int(region.y * height)
        x2 = int((region.x + region.width) * width)
        y2 = int((region.y + region.height) * height)
        color = (40, 42, 230) if region.color == "red" else (230, 120, 30)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, max(3, width // 600))
    if date_box:
        x, y, w, h = date_box
        cv2.rectangle(
            image,
            (int(x * width), int(y * height)),
            (int((x + w) * width), int((y + h) * height)),
            (48, 180, 65),
            max(3, width // 600),
        )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower() or ".jpg"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError("标注图片编码失败")
    encoded.tofile(str(destination))


def save_isolated_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    """Save colored ink from one stamp as high-contrast black-on-white OCR input."""
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    red, blue = _color_masks(crop)
    mask = red if region.color == "red" else blue
    mask = cv2.dilate(mask, np.ones((2, 2), np.uint8), iterations=1)
    canvas = np.full(crop.shape[:2], 255, dtype=np.uint8)
    canvas[mask > 0] = 0
    target_width = max(1000, canvas.shape[1])
    scale = target_width / max(1, canvas.shape[1])
    canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("印章裁剪图片编码失败")
    encoded.tofile(str(destination))


def save_color_isolated_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    """Keep only colored stamp ink on white while preserving stroke intensity.

    Binary isolation is useful for faint contours, but it removes the local
    intensity variation that OCR detectors sometimes need to separate curved
    Chinese glyphs.  This variant retains the original red/blue pixels and
    removes every neutral printed form pixel, so it is safe matching evidence
    even when the stamp overlaps the printed signature requirement.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    mask = _ocr_color_mask(crop, region.color)
    canvas = np.full_like(crop, 255)
    canvas[mask > 0] = crop[mask > 0]
    target_width = max(1000, canvas.shape[1])
    scale = target_width / max(1, canvas.shape[1])
    canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("保留章色裁剪图片编码失败")
    encoded.tofile(str(destination))


def save_rectangular_seal_code_line(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    """Save the lower numeric band of a rectangular station/code stamp.

    Heavy rectangular service stamps often contain small organization text in
    the upper half and one large 6--12 digit identifier in the lower half.
    Whole-stamp OCR can merge the border and upper text into the first digits.
    This derivative keeps only colored ink, removes long rectangle borders,
    and exposes the lower band as black-on-white OCR evidence.  It never uses
    neutral printed form text and is therefore safe for stamp matching.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("矩形编号章裁剪为空")
    crop_height, crop_width = crop.shape[:2]
    # Stay inside the rectangular border instead of trying to remove it with
    # long-line morphology.  The latter also erases adjoining wide digit
    # strokes on heavily inked real stamps.  Reviewed station stamps place the
    # large identifier in this lower inner band.
    band_y1 = max(0, int(crop_height * 0.44))
    band_y2 = min(crop_height, int(crop_height * 0.86))
    band_x1 = max(0, int(crop_width * 0.10))
    band_x2 = min(crop_width, int(crop_width * 0.92))
    band = crop[band_y1:band_y2, band_x1:band_x2]
    b, g, r = (channel.astype(np.int16) for channel in cv2.split(band))
    if region.color == "red":
        dominance = r - np.maximum(g, b)
    else:
        dominance = b - np.maximum(g, r)
    dominance = np.clip(dominance, 0, 255).astype(np.uint8)
    # A heavy stamp can leave a pale red/blue wash across the whole box.  The
    # large identifier remains much more channel-dominant than that wash.
    # Preserve the continuous dominance instead of hard-thresholding: the
    # recognition-only Paddle model needs local intensity variation to keep
    # adjacent digits such as 6/8 distinct.
    dominance = cv2.normalize(dominance, None, 0, 255, cv2.NORM_MINMAX)
    canvas = 255 - dominance
    target_width = max(1400, canvas.shape[1])
    scale = target_width / max(1, canvas.shape[1])
    canvas = cv2.resize(
        canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
    )
    canvas = cv2.copyMakeBorder(
        canvas, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("矩形编号章数字行编码失败")
    encoded.tofile(str(destination))


def save_region_crop(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise ValueError("印章原始区域编码失败")
    encoded.tofile(str(destination))


def read_image_size(path: str | Path) -> tuple[int, int]:
    """Return the source image ``(width, height)`` as the crop writers see it.

    EXIF rotation is already applied by the decoder, so the numbers here are in
    the same frame as :func:`save_region_crop` and the pixel boxes an external
    service reports.
    """
    image = _read_image(path)
    height, width = image.shape[:2]
    return width, height


def save_pixel_region_crop(
    source: str | Path,
    destination: str | Path,
    box: tuple[float, float, float, float],
) -> None:
    """Write the absolute pixel box ``(x1, y1, x2, y2)`` of the source image.

    :class:`SealRegion` carries page fractions because that is the currency of
    our own detection.  A box returned by an external service already *is*
    geometry, so it is written directly instead of being round-tripped through
    fractions first, which would move the corners by up to a pixel each.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(round(float(value))) for value in box)
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("印章区域超出图片范围")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise ValueError("印章区域编码失败")
    encoded.tofile(str(destination))


@timed('date_crop_generation')
def save_receipt_date_crop(
    source: str | Path,
    destination: str | Path,
    signature_anchor_y: float,
    *,
    tight: bool = True,
    raw_destination: str | Path | None = None,
    color_clean_destination: str | Path | None = None,
    left: float | None = None,
) -> tuple[float, float, float, float]:
    """Remove colored seal ink around the handwritten receiving date."""
    image = _read_image(source)
    height, width = image.shape[:2]
    if tight:
        x1n, x2n = 0.76, 0.995
        y1n = max(0.0, signature_anchor_y + 0.068)
        y2n = min(1.0, signature_anchor_y + 0.108)
    else:
        x1n, x2n = 0.72, 0.995
        y1n = max(0.0, signature_anchor_y + 0.052)
        y2n = min(1.0, signature_anchor_y + 0.112)
    if left is not None:
        x1n = max(0.0, min(float(left), x2n - 0.05))
    x1, x2 = int(x1n * width), int(x2n * width)
    y1, y2 = int(y1n * height), int(y2n * height)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise DateCropOutOfRange(
            f"日期裁剪区域超出页面范围：横向 {x1n:.3f}~{x2n:.3f}、"
            f"纵向 {y1n:.3f}~{y2n:.3f}（图片 {width}x{height}）"
        )
    if raw_destination:
        _write_stage_image(raw_destination, crop, ".jpg")
    # A broad HSV mask treats a dark handwritten stroke underneath a red
    # stamp as red as well, so it erases the very pixels needed by OCR.  Keep
    # the luminance of all dark ink and suppress only bright, strongly red
    # pixels.  This leaves a little pale stamp residue when the two inks are
    # physically fused, but it does not manufacture or delete handwriting.
    blue, green, red = (channel.astype(np.int16) for channel in cv2.split(crop))
    red_excess = red - np.maximum(green, blue)
    bright_red = (
        (red_excess >= 35)
        & (np.minimum(green, blue) >= 140)
        & (red >= 160)
    )
    cleaned_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    cleaned_gray[bright_red] = 255
    # The displayed derivative can remove the complete red chroma mask.  It
    # is intentionally separate from ``cleaned_gray``: the latter is kept for
    # OCR so dark handwriting at a stamp intersection is not erased merely to
    # make the preview look cleaner.
    red_mask, _ = _color_masks(crop)
    visual_cleaned = crop.copy()
    visual_cleaned[red_mask > 0] = (255, 255, 255)
    if color_clean_destination:
        _write_stage_image(color_clean_destination, visual_cleaned, ".png")
    gray = cleaned_gray
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(
        ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, ink.shape[1] // 9), 2)),
    )
    vertical = cv2.morphologyEx(
        ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, max(20, ink.shape[0] // 2))),
    )
    ink = cv2.subtract(ink, cv2.bitwise_or(horizontal, vertical))
    gray = cv2.bitwise_not(ink)
    target_width = 1500
    scale = target_width / max(1, gray.shape[1])
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", gray)
    if not ok:
        raise ValueError("日期裁剪图片编码失败")
    encoded.tofile(str(destination))
    return (x1n, y1n, x2n - x1n, y2n - y1n)


def _write_stage_image(destination: str | Path, image: np.ndarray, suffix: str) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError(f"中间图片编码失败: {destination.name}")
    encoded.tofile(str(destination))


def _robust_round_seal_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return a noise-trimmed near-square ink box for a distorted round seal.

    Sparse JPEG chroma speckles can sit far from the actual stamp. Using their
    absolute min/max moves the polar centre and leaves the company arc as a
    wave. This helper stays inactive unless trimming the 0.5% coordinate
    tails changes a clearly non-round raw box into a substantially smaller,
    near-square box while retaining the dense ink core.
    """
    ys, xs = np.where(mask > 0)
    if len(xs) < 200:
        return None
    raw_x1, raw_x2 = int(xs.min()), int(xs.max()) + 1
    raw_y1, raw_y2 = int(ys.min()), int(ys.max()) + 1
    raw_width, raw_height = raw_x2 - raw_x1, raw_y2 - raw_y1
    if raw_width <= 0 or raw_height <= 0:
        return None
    raw_aspect = raw_width / raw_height
    if 0.75 <= raw_aspect <= 1.33:
        return None

    quantile = 0.005
    x1 = int(np.quantile(xs, quantile))
    x2 = int(np.quantile(xs, 1 - quantile)) + 1
    y1 = int(np.quantile(ys, quantile))
    y2 = int(np.quantile(ys, 1 - quantile)) + 1
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0:
        return None
    aspect = width / height
    if not 0.80 <= aspect <= 1.25:
        return None
    if width * height > raw_width * raw_height * 0.82:
        return None
    pad_x = max(2, round(width * 0.035))
    pad_y = max(2, round(height * 0.035))
    return (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(mask.shape[1], x2 + pad_x),
        min(mask.shape[0], y2 + pad_y),
    )


def save_ellipse_normalized_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion | None = None,
    *,
    color: str = "red",
) -> Path:
    """Stretch a horizontal/vertical oval into a square, color-safe image.

    This is a real pipeline stage, not an internal resize hidden inside the
    polar unwrap.  The colored-ink bounding box is cropped with a small
    margin, its shorter axis is stretched to the longer axis, and the result
    is written as a visible white-background artifact.  Later oval OCR stages
    must consume this image so the oval's ring text is treated like a circle.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if region is not None:
        x1 = max(0, int(region.x * width))
        y1 = max(0, int(region.y * height))
        x2 = min(width, int((region.x + region.width) * width))
        y2 = min(height, int((region.y + region.height) * height))
        crop = image[y1:y2, x1:x2]
        ink_color = region.color
    else:
        crop = image
        ink_color = color
    if crop.size == 0:
        raise ValueError("椭圆章拉伸区域为空")
    mask = _ocr_color_mask(crop, ink_color)
    points = cv2.findNonZero(mask)
    if points is None:
        # Keep a useful visible intermediate even when the color is extremely
        # faint; the later OCR stage can then report a genuine empty result.
        normalized = crop.copy()
    else:
        bx, by, bw, bh = cv2.boundingRect(points)
        pad_x = max(3, round(bw * 0.035))
        pad_y = max(3, round(bh * 0.035))
        bx1, by1 = max(0, bx - pad_x), max(0, by - pad_y)
        bx2, by2 = min(crop.shape[1], bx + bw + pad_x), min(crop.shape[0], by + bh + pad_y)
        crop = crop[by1:by2, bx1:bx2]
        mask = mask[by1:by2, bx1:bx2]
        normalized = np.full_like(crop, 255)
        normalized[mask > 0] = crop[mask > 0]
    normalized_height, normalized_width = normalized.shape[:2]
    if normalized_height <= 0 or normalized_width <= 0:
        raise ValueError("椭圆章拉伸图片为空")
    side = max(normalized_height, normalized_width)
    normalized = cv2.resize(
        normalized,
        (side, side),
        interpolation=cv2.INTER_CUBIC,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", normalized)
    if not ok:
        raise ValueError("椭圆章拉伸图片编码失败")
    encoded.tofile(str(destination))
    return destination


def _save_ellipse_annulus_unwrapped(
    gray: np.ndarray,
    mask: np.ndarray,
    destination: str | Path,
) -> bool:
    """Unwrap a normalized oval's annulus with ellipse-aware sampling.

    ``warpPolar`` assumes both boundaries are concentric circles.  Even after
    an oval is stretched, the printed inner/outer borders are not perfectly
    concentric, so polar sampling creates the duplicated/wavy text seen in
    the v6 result.  Fit the two visible ellipse borders and sample between
    them directly instead.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    fitted = []
    for contour in contours:
        if len(contour) < 20 or cv2.contourArea(contour) < mask.size * 0.01:
            continue
        try:
            ellipse = cv2.fitEllipse(contour)
            (cx, cy), (axis_a, axis_b), angle = ellipse
            mean_axis = (float(axis_a) + float(axis_b)) / 2.0
            if mean_axis <= 0:
                continue
            points = contour[:, 0].astype(np.float32)
            theta = np.deg2rad(float(angle))
            dx, dy = points[:, 0] - cx, points[:, 1] - cy
            x_rot = dx * np.cos(theta) + dy * np.sin(theta)
            y_rot = -dx * np.sin(theta) + dy * np.cos(theta)
            radial_error = np.sqrt(
                (x_rot / (float(axis_a) / 2.0)) ** 2
                + (y_rot / (float(axis_b) / 2.0)) ** 2
            )
            fitted.append(
                (mean_axis, float(np.median(np.abs(radial_error - 1.0))), ellipse)
            )
        except (cv2.error, ValueError, ZeroDivisionError):
            continue
    if len(fitted) < 2:
        return False
    # Reject contours that are mostly connected glyph blobs; real oval
    # borders have a much tighter ellipse residual.
    clean = [item for item in fitted if item[1] <= 0.06]
    if len(clean) < 2:
        return False
    outer = max(clean, key=lambda item: item[0])
    inner_candidates = [
        item for item in clean
        if outer[0] * 0.35 < item[0] < outer[0] * 0.88
    ]
    if not inner_candidates:
        return False
    inner = max(inner_candidates, key=lambda item: item[0])
    (outer_cx, outer_cy), _, _ = outer[2]
    (inner_cx, inner_cy), _, _ = inner[2]
    center_x = (float(outer_cx) + float(inner_cx)) / 2.0
    center_y = (float(outer_cy) + float(inner_cy)) / 2.0
    width = gray.shape[1]
    output_width = max(1200, min(2200, width * 2))
    output_height = max(96, round(output_width * 0.065))

    def radius(ellipse, angles):
        (_, _), (axis_a, axis_b), angle = ellipse
        radians = angles - np.deg2rad(float(angle))
        return 1.0 / np.sqrt(
            (np.cos(radians) / (float(axis_a) / 2.0)) ** 2
            + (np.sin(radians) / (float(axis_b) / 2.0)) ** 2
        )

    strips = []
    for phase in (0.0, 2.0 * np.pi / 3.0, 4.0 * np.pi / 3.0):
        angles = np.linspace(
            np.pi / 2.0 + phase,
            np.pi / 2.0 + phase + 2.0 * np.pi,
            output_width,
            endpoint=False,
        )
        outer_radius = radius(outer[2], angles) * 0.98
        inner_radius = radius(inner[2], angles) * 1.08
        fraction = np.linspace(0.0, 1.0, output_height, dtype=np.float32)[:, None]
        radial = outer_radius[None, :] * (1.0 - fraction) + inner_radius[None, :] * fraction
        map_x = (center_x + radial * np.cos(angles)[None, :]).astype(np.float32)
        map_y = (center_y + radial * np.sin(angles)[None, :]).astype(np.float32)
        strips.append(
            cv2.remap(
                gray,
                map_x,
                map_y,
                cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=255,
            )
        )
    gap = np.full((18, output_width), 255, dtype=np.uint8)
    output = np.vstack((strips[0], gap, strips[1], gap, strips[2]))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", output)
    if not ok:
        raise ValueError("椭圆环形文字展开图片编码失败")
    encoded.tofile(str(destination))
    return True


def save_unwrapped_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion | None,
    *,
    robust_bounds: bool = False,
    exclude_boxes: list[tuple[float, float, float, float]] | None = None,
    elliptical: bool = False,
    color: str = "red",
    normalized: bool = False,
) -> bool:
    """Unwrap circular stamp text into a horizontal line for conventional OCR.

    ``exclude_boxes`` contains normalized boxes within the extracted stamp
    crop.  They are whitened only in this ring-text derivative, so a detected
    horizontal stamp-type row cannot contaminate the circular company-name
    OCR while the original/color/type-band evidence remains auditable.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if region is None:
        # The ellipse route may already have rotated and colour-isolated the
        # QingTong crop.  In that case the whole file is the local stamp and
        # no page-coordinate region should be applied a second time.
        crop = image
    else:
        x1, y1 = int(region.x * width), int(region.y * height)
        x2 = int((region.x + region.width) * width)
        y2 = int((region.y + region.height) * height)
        crop = image[y1:y2, x1:x2]
    # Use the same strict chroma gate as the visible color-isolated artifact.
    # The permissive detector mask is intentionally unsuitable here: faint
    # red JPEG fringes around black table lines become thick horizontal bars
    # after polar unwrapping and can erase the curved company name.
    mask = _ocr_color_mask(crop, region.color if region is not None else color)
    for box in exclude_boxes or ():
        if not box or len(box) != 4:
            continue
        left, top, right, bottom = box
        x1 = max(0, min(mask.shape[1], round(float(left) * mask.shape[1])))
        y1 = max(0, min(mask.shape[0], round(float(top) * mask.shape[0])))
        x2 = max(x1, min(mask.shape[1], round(float(right) * mask.shape[1])))
        y2 = max(y1, min(mask.shape[0], round(float(bottom) * mask.shape[0])))
        mask[y1:y2, x1:x2] = 0
    canvas = np.full(mask.shape, 255, dtype=np.uint8)
    canvas[mask > 0] = 0
    h, w = canvas.shape
    # Rectangular stamps are already linear. Polar unwrapping would bend otherwise
    # readable rows into arcs, so keep a normalized high-resolution view instead.
    # A circular stamp crop often becomes moderately wide (1.45--1.60) when
    # handwriting or a nearby duplicate is joined to its contour.  Treating
    # that crop as a rectangular stamp prevents polar unwrapping of the curved
    # company name.  Reviewed service-center rectangles are materially wider;
    # 1.65 keeps those linear while recovering the ambiguous circular cases.
    if region is not None and seal_region_is_rectangular(source, region):
        scale = 1500 / max(1, w)
        normalized = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", normalized)
        if not ok:
            raise ValueError("矩形印章校正图片编码失败")
        encoded.tofile(str(destination))
        return False
    if elliptical and normalized:
        # Keep anti-aliased red strokes for the v6 recognizer; hard threshold
        # turns the thin oval company glyphs into broken blobs.
        b, g, r = (channel.astype(np.float32) for channel in cv2.split(crop))
        if (region.color if region is not None else color) == "red":
            dominance = r - np.maximum(g, b)
        else:
            dominance = b - np.maximum(g, r)
        gray = 255.0 - np.clip(dominance * (255.0 / 160.0), 0.0, 255.0)
        if _save_ellipse_annulus_unwrapped(
            gray.astype(np.uint8), mask, destination
        ):
            return False
    # Normalize the colored-ink bounding box to a square before polar
    # unwrapping.  Padding a 2:1 oval into a square leaves its text on an
    # ellipse, which a circular polar transform bends into waves.  Stretching
    # only this OCR derivative preserves the user-visible original crop while
    # making oval company text horizontal enough for conventional OCR.
    points = cv2.findNonZero(mask)
    used_robust_bounds = False
    if points is not None and not normalized:
        robust = _robust_round_seal_bounds(mask) if robust_bounds else None
        if robust is not None:
            bx1, by1, bx2, by2 = robust
            used_robust_bounds = True
        else:
            bx, by, bw, bh = cv2.boundingRect(points)
            pad_x = max(2, round(bw * 0.035))
            pad_y = max(2, round(bh * 0.035))
            bx1, by1 = max(0, bx - pad_x), max(0, by - pad_y)
            bx2, by2 = min(w, bx + bw + pad_x), min(h, by + bh + pad_y)
        canvas = canvas[by1:by2, bx1:bx2]
    size = max(canvas.shape[:2])
    square = (
        canvas
        if normalized and canvas.shape[0] == canvas.shape[1]
        else cv2.resize(canvas, (size, size), interpolation=cv2.INTER_CUBIC)
    )
    radius = size / 2
    polar = cv2.warpPolar(
        square,
        (int(radius), 1440),
        (size / 2, size / 2),
        radius,
        cv2.WARP_POLAR_LINEAR | cv2.WARP_FILL_OUTLIERS,
    )
    strips = []
    for shift in (0, 480, 960):
        shifted = np.roll(polar, shift, axis=0)
        # Drop the outermost border ring.  After unwrapping it becomes a
        # full-width black bar above/below every text strip and can dominate
        # OCR detection.  Company glyphs sit just inside this border, so keep
        # the 42%–94% radial band rather than the complete outer radius.
        inner_radius, outer_radius = (
            (0.54, 0.90) if elliptical else (0.42, 0.94)
        )
        outer = shifted[:, int(radius * inner_radius) : int(radius * outer_radius)]
        strip = cv2.rotate(outer, cv2.ROTATE_90_COUNTERCLOCKWISE)
        strips.append(cv2.resize(strip, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC))
    gap = np.full((18, max(strip.shape[1] for strip in strips)), 255, dtype=np.uint8)
    normalized = [
        cv2.copyMakeBorder(strip, 0, 0, 0, gap.shape[1] - strip.shape[1], cv2.BORDER_CONSTANT, value=255)
        for strip in strips
    ]
    strip = normalized[0]
    for extra in normalized[1:]:
        strip = np.vstack((strip, gap, extra))
    # The circular/oval outline becomes one or two very long horizontal arcs
    # after polar unwrapping. Remove only strokes spanning at least one fifth
    # of the output width; Chinese glyph components are far shorter, so their
    # evidence remains intact while the intermediate image becomes auditable
    # and substantially easier for text detection.
    unwrapped_ink = cv2.bitwise_not(strip)
    border_lines = cv2.morphologyEx(
        unwrapped_ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(80, strip.shape[1] // 5), 2)
        ),
    )
    border_lines = cv2.dilate(
        border_lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 5))
    )
    strip = cv2.bitwise_not(cv2.subtract(unwrapped_ink, border_lines))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", strip)
    if not ok:
        raise ValueError("圆形印章展开图片编码失败")
    encoded.tofile(str(destination))
    return used_robust_bounds


def save_unwrapped_seal_bands(
    source: str | Path,
    destination_prefix: str | Path,
) -> list[Path]:
    """Save the three independent polar strips used by round-seal OCR.

    ``save_unwrapped_seal`` deliberately keeps three angular shifts in one
    auditable image.  On very shallow seals, feeding that tall contact sheet
    to OCR can shrink each company line too aggressively.  This helper only
    accepts the exact current 3-strip/18-pixel-gap layout and never guesses at
    legacy artifacts.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    strip_height, remainder = divmod(height - 36, 3)
    if remainder or strip_height <= 0:
        return []
    prefix = Path(destination_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    output = []
    for index in range(3):
        top = index * (strip_height + 18)
        band = image[top : top + strip_height, :width]
        destination = prefix.with_name(f"{prefix.stem}-{index + 1}.png")
        ok, encoded = cv2.imencode(".png", band)
        if not ok:
            raise ValueError("圆章展开分带图片编码失败")
        encoded.tofile(str(destination))
        output.append(destination)
    return output


def save_rectangular_seal_bands(
    source: str | Path,
    destination_prefix: str | Path,
) -> list[Path]:
    """Split a color-safe normalized rectangular stamp into three OCR rows.

    Each third is inset by about 0.8% at internal boundaries.  The narrow white
    separation keeps the company row from being detected together with the
    centre/bottom stamp type, which caused ``电子`` to regress to ``电于`` in
    a reviewed rectangular seal.  The complete normalized image remains an
    independent visible artifact, so these focused OCR rows are additive.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if height < 30 or width < 30:
        return []
    inset = max(2, round(height * 0.008))
    prefix = Path(destination_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    output = []
    for index in range(3):
        top = round(index * height / 3) + (inset if index else 0)
        bottom = min(
            height,
            round((index + 1) * height / 3)
            - (inset if index < 2 else 0),
        )
        band = image[top:bottom, :width]
        destination = prefix.with_name(f"{prefix.stem}-{index + 1}.png")
        ok, encoded = cv2.imencode(".png", band)
        if not ok:
            raise ValueError("矩形印章横向分带图片编码失败")
        encoded.tofile(str(destination))
        output.append(destination)
    return output


def save_round_seal_type_band(
    source: str | Path,
    destination: str | Path,
    *,
    orientation_aligned: bool = False,
    focus_box: list[float] | tuple[float, float, float, float] | None = None,
) -> Path:
    """Save the inner type row of a color-isolated round stamp.

    Reviewed circular customer stamps commonly put the legal company around
    the rim and a horizontal type such as ``手机售后专用章`` inside it.
    ``orientation_aligned`` is used after the stamp-type polygon has rotated
    the crop upright; that layout puts the row around the centre of the
    expanded canvas.  The legacy lower-inner slice remains the default for
    unrotated crops.  ``source`` must already be a color-only white-background
    derivative, so the focused band cannot import the black printed signature
    requirement into seal-matching evidence.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if height < 40 or width < 40:
        raise ValueError("圆章章类型分带图片过小")
    left, top, right, bottom = round_seal_type_band_box(
        source, orientation_aligned=orientation_aligned, focus_box=focus_box
    )
    band = image[top:bottom, left:right]
    if band.size == 0:
        raise ValueError("圆章章类型分带为空")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", band)
    if not ok:
        raise ValueError("圆章章类型分带图片编码失败")
    encoded.tofile(str(destination))
    return destination


def round_seal_type_band_box(
    source: str | Path,
    *,
    orientation_aligned: bool = False,
    focus_box: list[float] | tuple[float, float, float, float] | None = None,
) -> tuple[int, int, int, int]:
    """Return the pixel box used by :func:`save_round_seal_type_band`."""
    image = _read_image(source)
    height, width = image.shape[:2]
    if height < 40 or width < 40:
        raise ValueError("圆章章类型分带图片过小")
    if focus_box and len(focus_box) == 4:
        left = max(0, round(float(focus_box[0])))
        top = max(0, round(float(focus_box[1])))
        right = min(width, round(float(focus_box[2])))
        bottom = min(height, round(float(focus_box[3])))
    else:
        left, right = round(width * 0.08), round(width * 0.92)
        if orientation_aligned:
            top, bottom = round(height * 0.42), round(height * 0.62)
        else:
            top, bottom = round(height * 0.56), round(height * 0.84)
    return left, top, right, bottom
