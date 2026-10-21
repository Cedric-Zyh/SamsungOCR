"""Reading order for a tall left block beside two stacked right blocks."""
import numpy as np


def bounds(box):
    points = np.asarray(box["points"], dtype=float)
    return np.r_[points.min(0), points.max(0)]


def _overlap(a, b, axis):
    return max(0, min(a[axis + 2], b[axis + 2]) - max(a[axis], b[axis]))


def column_groups(boxes):
    rectangles = [bounds(box) for box in boxes]
    found = []
    for i, a in enumerate(rectangles):
        aw, ah = a[2:] - a[:2]
        for j, b in enumerate(rectangles):
            bw, bh = b[2:] - b[:2]
            if i == j or a[0] + a[2] >= b[0] + b[2] or a[2] > b[0] + .2 * min(aw, bw):
                continue
            for k, c in enumerate(rectangles):
                cw, ch = c[2:] - c[:2]
                if k in (i, j) or b[1] >= c[1]:
                    continue
                if _overlap(b, c, 0) < .5 * min(bw, cw) or _overlap(b, c, 1) > .3 * min(bh, ch):
                    continue
                if ah < 1.2 * min(bh, ch) or aw < 1.3 * ah:
                    continue
                if not all(a[1] - .15 * ah < (r[1] + r[3]) / 2 < a[3] + .15 * ah for r in (b, c)):
                    continue
                if a[2] <= c[0] + .2 * min(aw, cw):
                    found.append([i, j, k])
    return found


def reading_order(boxes, groups):
    assigned, items = set(), []
    for group in groups:
        if any(i in assigned for i in group):
            continue
        assigned.update(group)
        rectangles = [bounds(boxes[i]) for i in group]
        items.append((min(r[1] for r in rectangles), min(r[0] for r in rectangles), group))
    for i, box in enumerate(boxes):
        if i not in assigned:
            rectangle = bounds(box)
            items.append((rectangle[1], rectangle[0], [i]))
    return [i for _, _, group in sorted(items) for i in group]
