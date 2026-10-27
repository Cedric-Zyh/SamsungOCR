"""Continuous source-chroma enhancement for a separately verified pale red grid."""
import cv2
import numpy as np


def pale_red_statistics(image):
    """Restrict the stronger view to genuinely faint observed red ink."""
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 30):
        return None
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(image))
    # Pale magenta ink can have red and blue equally bright. Subtracting
    # blue erases those real strokes; green is the opposing channel here.
    chroma = r-g
    hue = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 0]
    values = chroma[(chroma > 4) & ((hue <= 20) | (hue >= 145))]
    if len(values) < max(200, image.shape[0]*image.shape[1]*.01):
        return None
    median, high = map(float, np.quantile(values, (.5, .9)))
    if median > 35 or high > 90:
        return None
    return {"signal": "red_green_with_pink_hue",
            "source_chroma_median": median, "source_chroma_p90": high,
            "supported_pixels": len(values)}


def enhance_pale_red(image):
    """Amplify real colour differences without growing or completing strokes.

    Callers must verify the full ruled grid first. Geometry thresholds are
    never applied to these OCR pixels; the source array is never modified.
    """
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(image))
    delta = r-g
    hue = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 0]
    fraction = delta/np.maximum(255-np.maximum(b, g), 1)
    black = (r < 115) & (fraction < .03)
    nearby = cv2.distanceTransform((~black).astype(np.uint8), cv2.DIST_L2, 5) <= 1.5
    supported = ((hue <= 20) | (hue >= 145)) & ~black & ~(nearby & (fraction < .08))
    strength = np.clip(np.maximum(0, delta)*6, 0, 235)
    strength[~supported] = 0
    result = np.full_like(image, 255)
    result[:, :, 0] = result[:, :, 1] = np.rint(255-strength).astype(np.uint8)
    return result
