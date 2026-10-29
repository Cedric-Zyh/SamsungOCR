"""Color-clean copies for printed-page OCR; source pixels stay untouched."""

from receipt_ocr.runtime.image_processing import timed_image
from PIL import Image, ImageChops, ImageOps

from .io import _read_image


def suppress_stamp_color(image):
    red, green, blue = image.convert("RGB").split()
    return ImageOps.autocontrast(ImageChops.lighter(red, ImageChops.lighter(green, blue)), cutoff=1)


@timed_image('denoise', '去噪与干扰清理')
def printed_page_image(source):
    # Use the same decoded coordinate frame as the original date/seal crops.
    frame = _read_image(source)
    if frame.ndim == 3:
        frame = frame[:, :, ::-1]  # Decoder supplies BGR.
    return suppress_stamp_color(Image.fromarray(frame.astype("uint8")))
