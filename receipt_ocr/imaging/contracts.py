"""Image geometry and crop errors."""
from __future__ import annotations

from dataclasses import asdict, dataclass


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
