"""Conservative round-ring calibration; analysis thresholds never alter OCR ink."""

from receipt_ocr.runtime.image_processing import timed_image
import cv2
import numpy as np

from .border_fit import fit_border_contour
from .colors import _ocr_color_mask
from .ellipse import _ellipse_fits_canvas, _ellipse_normal_maps, _fit_ellipse_annulus
from .ring_border import RingGuide


def _chroma(image, color):
    blue, green, red = (channel.astype(np.float32) for channel in cv2.split(image))
    return np.maximum(0, red - np.maximum(blue, green) if color == "red"
                      else blue - np.maximum(green, red))


def _border_distance(points, fitted):
    """First-order geometric distance in source pixels, for held-out checks."""
    (cx, cy), (a, b), angle = fitted
    a, b = a / 2, b / 2
    co, si = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
    dx, dy = points[:, 0] - cx, points[:, 1] - cy
    u, v = dx * co + dy * si, -dx * si + dy * co
    return np.abs((u/a)**2 + (v/b)**2 - 1) / np.maximum(2*np.hypot(u/a**2, v/b**2), 1e-6)


def _observed_outer(image, color):
    strength = _chroma(image, color)
    colored = strength[strength > 5]
    if colored.size < 200:
        return None, {"reason": "insufficient_ink"}
    level = float(np.quantile(colored, .9))
    fits = []
    for ratio in (.35, .55, .75):
        geometry = _fit_ellipse_annulus((strength > level*ratio).astype(np.uint8)*255)
        if geometry is None:
            return None, {"reason": "missing_strength_fit"}
        fits.append(geometry[0][2])
    centers = np.array([fit[0] for fit in fits])
    axes = np.array([sorted(fit[1]) for fit in fits])
    radius = float(np.median(axes)) / 2
    if (radius <= 0 or not np.isfinite(axes).all()
            or np.max(np.ptp(centers, axis=0)) > .035*radius
            or np.max(np.ptp(axes, axis=0)) > .08*radius):
        return None, {"reason": "unstable_strength_geometry"}
    (cx, cy), (aa, bb), angle = fits[1]
    a, b = aa / 2, bb / 2
    co, si = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
    theta = np.linspace(0, 2*np.pi, 720, endpoint=False)
    x = cx + a*np.cos(theta)*co - b*np.sin(theta)*si
    y = cy + a*np.cos(theta)*si + b*np.sin(theta)*co
    nx = np.cos(theta)/a*co - np.sin(theta)/b*si
    ny = np.cos(theta)/a*si + np.sin(theta)/b*co
    norm = np.hypot(nx, ny)
    nx, ny = nx/norm, ny/norm
    reach = max(10, round(radius*.14))
    offsets = np.arange(-reach, reach+1)
    mx = (x[None, :] + offsets[:, None]*nx).astype(np.float32)
    my = (y[None, :] + offsets[:, None]*ny).astype(np.float32)
    values = cv2.remap(strength, mx, my, cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    values = cv2.GaussianBlur(values, (3, 3), 0)
    points = np.full((len(theta), 2), np.nan)
    valid = np.zeros(len(theta), bool)
    for index in range(len(theta)):
        profile = values[:, index]
        peak = float(np.quantile(profile, .95))
        if peak < level*.2:
            continue
        active = profile > max(20, peak*.55)
        changes = np.flatnonzero(np.diff(np.r_[False, active, False]))
        runs = [(int(s), int(e)) for s, e in zip(changes[::2], changes[1::2])
                if 3 <= e-s <= radius*.12]
        if not runs:
            continue
        start, end = min(runs, key=lambda run: abs((offsets[run[0]]+offsets[run[1]-1])/2))
        if abs((offsets[start]+offsets[end-1])/2) > reach*.8:
            continue
        off = float(offsets[end-1])
        points[index] = [x[index]+off*nx[index], y[index]+off*ny[index]]
        valid[index] = True
    if valid.mean() < .7:
        return None, {"reason": "insufficient_observed_border", "coverage": float(valid.mean())}
    # Interleaved held-out angles never participate in the border fit.
    validation = np.arange(len(theta)) % 4 == 0
    fitted = fit_border_contour(points[valid & ~validation].astype(np.float32).reshape(-1, 1, 2))
    if not _ellipse_fits_canvas(fitted, image.shape) or max(fitted[1]) > min(fitted[1])*1.25:
        return None, {"reason": "unsupported_round_geometry"}
    distances = _border_distance(points[valid], fitted)
    supported = valid.copy()
    supported[np.flatnonzero(valid)[distances > max(6, radius*.015)]] = False
    longest = gap = 0
    for present in np.tile(supported, 2):
        gap = 0 if present else gap+1
        longest = max(longest, gap)
    if supported.mean() < .7 or longest > len(theta)*.18:
        return None, {"reason": "unreliable_border_support", "coverage": float(supported.mean())}
    held = points[valid & validation]
    errors = _border_distance(held, fitted)
    evidence = {"reason": "observed_rim", "threshold_ellipses": fits, "ellipse": fitted,
                "coverage": float(supported.mean()), "longest_gap_degrees": longest/2,
                "validation_median_px": float(np.median(errors)),
                "validation_p95_px": float(np.quantile(errors, .95))}
    if evidence["validation_median_px"] > radius*.012:
        return None, {**evidence, "reason": "validation_error"}
    outer = (float(sum(fitted[1])/2), float(np.median(distances)/radius), fitted)
    # Draw the fitted border, with support derived from actual angular probes.
    (cx, cy), (aa, bb), angle = fitted
    co, si = np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))
    border = np.column_stack((cx+aa/2*np.cos(theta)*co-bb/2*np.sin(theta)*si,
                              cy+aa/2*np.cos(theta)*si+bb/2*np.sin(theta)*co))
    return (outer, border, supported, held), evidence


def _letter_edge_maps(gray, geometry):
    """Measure a safe inner sampling envelope on the already white-masked image."""
    outer = geometry[0]
    radius = min(outer[2][1]) / 2
    seed = _ellipse_normal_maps(outer, geometry[1], True)
    dx, dy = seed[0][-1]-seed[0][0], seed[1][-1]-seed[1][0]
    lengths = np.hypot(dx, dy)
    ux, uy = dx/lengths, dy/lengths
    width = seed[0].shape[1]
    fractions = np.linspace(.02, .65, max(160, round(radius*.63)))
    depth = ((fractions-.05)*radius)[:, None]
    probe_x = (seed[0][0][None, :]+depth*ux).astype(np.float32)
    probe_y = (seed[1][0][None, :]+depth*uy).astype(np.float32)
    probe = cv2.remap(gray, probe_x, probe_y, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    height = probe.shape[0]
    probes = []
    for threshold in (210, 160):
        ink = (probe < threshold).astype(np.uint8)
        # Detach the border only on this measurement copy, never in OCR pixels.
        ink[fractions < .14] = 0
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(ink)
        candidates = []
        for label, (x, y, w, h, area) in enumerate(stats[1:], 1):
            inward = float(fractions[min(y+h-1, height-1)])
            outward = float(fractions[y])
            if (area >= max(30, height*.2) and inward-outward >= .10
                    and outward <= .30 and 8 <= w <= width*.85):
                candidates.append((label, int(x), int(y), int(w), int(h), inward))
        occupied = np.zeros(width, bool)
        deepest = np.full(width, -1, np.int32)
        for _, x, y, w, h, _ in candidates:
            occupied[x:x+w] = True
            deepest[x:x+w] = np.maximum(deepest[x:x+w], y+h-1)
        coverage = float(occupied.mean())
        if len(candidates) < 6 or coverage < .35:
            return None, {"reason": "insufficient_glyph_support", "threshold": threshold,
                          "glyph_components": len(candidates), "coverage": coverage}
        chosen = {part[0] for part in candidates}
        depths = [part[-1] for part in candidates]
        detached = 0
        for label, (x, y, w, h, area) in enumerate(stats[1:], 1):
            if label in chosen or area < 10 or w > width*.15:
                continue
            near = deepest[max(0, x-5):min(width, x+w+5)]
            edge = int(near.max()) if near.size else -1
            if edge >= 0 and y <= edge+max(3, round(radius*.08)) and y+h-1 > edge:
                depths.append(float(fractions[min(y+h-1, height-1)]))
                detached += 1
        probes.append({"threshold": threshold, "glyph_components": len(candidates),
                       "detached_strokes": detached, "coverage": coverage,
                       "innermost_depth": max(depths)})
    spread = abs(probes[0]["innermost_depth"]-probes[1]["innermost_depth"])
    evidence = {"probes": probes, "depth_spread": spread, "outer_radius_px": radius}
    if spread > .04:
        return None, {**evidence, "reason": "unstable_inner_edge"}
    margin = max(8/radius, .035)
    inner_depth = max(probe["innermost_depth"] for probe in probes)+margin
    if inner_depth > .63:
        return None, {**evidence, "reason": "ink_reaches_probe_limit"}
    scale = (seed[0].shape[0]-1) / float(np.median(lengths))
    out_height = max(96, round((inner_depth-.05)*radius*scale))
    depth = ((np.linspace(.05, inner_depth, out_height)-.05)*radius)[:, None]
    maps = ((seed[0][0][None, :]+depth*ux).astype(np.float32),
            (seed[1][0][None, :]+depth*uy).astype(np.float32))
    return maps, {**evidence, "reason": "supported_letter_edges",
                  "boundary_kind": "conservative_sampling_envelope",
                  "inner_radius_fraction": 1-inner_depth,
                  "safety_margin_px": margin*radius, "sampling_height": out_height}


@timed_image('normalize', '形状校正')
def calibrate_round_ring(intact, masked, *, color="red"):
    """Return a shared guide only for a poor fit with independently better support.

    Outer geometry uses intact pixels; the inner envelope uses masked pixels in
    the same coordinate frame. No OCR text or expected company name is accepted.
    A skipped/uncertain result leaves the existing input path entirely intact.
    """
    evidence = {"method": "round_observed_rim_letter_band", "status": "fallback"}
    if (color not in {"red", "blue"} or intact is None or masked is None
            or intact.shape != masked.shape):
        return None, {**evidence, "reason": "unsupported_color_or_frame"}
    base = _fit_ellipse_annulus(_ocr_color_mask(intact, color))
    if base is None:
        return None, {**evidence, "reason": "missing_base_geometry"}
    evidence["base_fit_error"] = base[0][1]
    if base[0][1] <= .025:
        return None, {**evidence, "status": "skipped", "reason": "ordinary_fit_sufficient"}
    if max(base[0][2][1]) > min(base[0][2][1])*1.25:
        return None, {**evidence, "reason": "unsupported_round_geometry"}
    candidate, border_evidence = _observed_outer(intact, color)
    evidence["outer"] = border_evidence
    if candidate is None:
        return None, {**evidence, "reason": border_evidence["reason"]}
    outer, points, supported, held = candidate
    base_error = float(np.median(_border_distance(held, base[0][2])))
    evidence["base_validation_median_px"] = base_error
    # A new fit needs a material improvement on angles excluded from its fit.
    if border_evidence["validation_median_px"] >= base_error*.75:
        return None, {**evidence, "reason": "no_validated_improvement"}
    geometry = (outer, base[1], True)
    gray = (255-np.clip(_chroma(masked, color)*255/160, 0, 255)).astype(np.uint8)
    maps, inner_evidence = _letter_edge_maps(gray, geometry)
    evidence["inner"] = inner_evidence
    if maps is None:
        return None, {**evidence, "reason": inner_evidence["reason"]}
    guide = RingGuide(geometry, masked.shape[:2], points, supported, *maps)
    return guide, {**evidence, "status": "applied", "reason": "validated_rim_and_letter_band",
                   "source_size": list(masked.shape[1::-1]), "unwrap_size": list(maps[0].shape[::-1])}
