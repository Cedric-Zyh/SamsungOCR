"""Local server entry point.

The Flask application and route composition live in
:mod:`receipt_ocr.web.application`.
"""

import sys

from receipt_ocr.runtime.model_paths import configure_model_environment

if getattr(sys, "frozen", False):
    # PaddleX fixes its cache root at import time, before any model is loaded.
    configure_model_environment()

from receipt_ocr.web.application import app, run
from receipt_ocr.web import application as _application


def __getattr__(name):
    """Resolve old script resource names from the current runtime owner."""
    return getattr(_application, name)


if __name__ == "__main__":
    run()
