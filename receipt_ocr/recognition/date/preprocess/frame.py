"""Conservative frame removal; preserve interior digits and crossings."""
from pathlib import Path
from PIL import Image

def _save_positioned_outer_frame_clean(
    source: str | Path,
    destination: str | Path,
) -> None:
    """Remove only the date-row frame, leaving interior digit strokes intact.

    The existing table-line view is deliberately conservative and can leave
    the row's outer rules visible.  This audit-only view uses the geometry of
    the date line: long horizontal rules in the upper/lower bands and long
    vertical rules at the image edges are treated as frame pixels.  Interior
    vertical strokes (notably a handwritten ``1`` before ``日``) are not
    removed merely because they are vertical.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
        import numpy as np

        with Image.open(source) as opened:
            rgb = opened.convert("RGB")
            array = np.asarray(rgb).copy()
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        ink = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY_INV)[1]
        height, width = ink.shape

        # Locate the entire rule (including pale antialiasing), then protect
        # columns with nearby ink on BOTH sides of that rule. Delete only the
        # unprotected parts of the narrow band, never reconstruct handwriting.
        band_mask = np.zeros_like(ink)
        protected = np.zeros_like(ink)
        pale_ink = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)[1]
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(30, width // 8), 1)
        )
        horizontal = cv2.morphologyEx(pale_ink, cv2.MORPH_OPEN, horizontal_kernel)
        row_density = np.count_nonzero(horizontal, axis=1) / max(1, width)
        upper_limit = round(height * 0.38)
        lower_start = round(height * 0.52)
        for start, stop in ((0, upper_limit), (lower_start, height)):
            candidate_rows = np.flatnonzero(row_density[start:stop] >= 0.45) + start
            if candidate_rows.size:
                groups = np.split(
                    candidate_rows,
                    np.where(np.diff(candidate_rows) > 1)[0] + 1,
                )
                for group in groups:
                    if not group.size:
                        continue
                    first = max(0, int(group[0]) - 1)
                    last = min(height, int(group[-1]) + 2)
                    depth = max(3, round(height * 0.045))
                    # Allow a slight horizontal shift for slanted 1/7 strokes.
                    radius = max(2, round(height * 0.025))
                    kernel = np.ones((1, radius * 2 + 1), np.uint8)
                    above = (ink[max(0, first - depth):first] > 0).any(axis=0)
                    below = (ink[last:min(height, last + depth)] > 0).any(axis=0)
                    above = cv2.dilate(above.astype(np.uint8)[None, :], kernel)[0]
                    below = cv2.dilate(below.astype(np.uint8)[None, :], kernel)[0]
                    crossing = cv2.dilate(
                        (above & below)[None, :], np.ones((1, 3), np.uint8)
                    )[0] > 0
                    columns = np.flatnonzero(horizontal[group].any(axis=0))
                    if not columns.size:
                        continue
                    left = max(0, int(columns[0]) - 1)
                    right = min(width, int(columns[-1]) + 2)
                    band_mask[first:last, left:right] = 255
                    protected[first:last, crossing] = 255

        # Remove a vertical frame only when it is at the extreme left/right
        # edge.  The common interior vertical stroke before ``日`` is kept.
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(12, height // 5))
        )
        vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, vertical_kernel)
        edge_mask = np.zeros_like(vertical)
        # The printed ``日`` cell can leave its right border several pixels
        # inside the crop edge.  A wider right-only band removes that border
        # while leaving the handwritten day and the separator before ``日``.
        edge_width = max(2, round(width * 0.08))
        edge_mask[:, :edge_width] = vertical[:, :edge_width]
        edge_start = max(0, width - edge_width)
        edge_mask[:, edge_start:] = vertical[:, edge_start:]

        mask = cv2.dilate(
            cv2.bitwise_or(band_mask, edge_mask),
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)),
        )
        # Apply protection last, so mask dilation cannot eat the crossings.
        mask[protected > 0] = 0
        cleaned = array.copy()
        cleaned[mask > 0] = 255
        Image.fromarray(cleaned).save(destination)
    except Exception:
        # The extra view is for visual audit only; preserve the pipeline if
        # optional image tooling is unavailable.
        with Image.open(source) as opened:
            opened.convert("RGB").save(destination)

