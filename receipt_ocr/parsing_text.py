"""Text normalization shared by independent receipt parsers."""

from __future__ import annotations

import re


def normalize_text(text: str) -> str:
    return re.sub(r"[\s:：,，.。()（）\[\]【】\-_/]", "", text).lower()
