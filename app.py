"""Local server entry point.

The Flask application and route composition live in
:mod:`receipt_ocr.web.application`.
"""

from receipt_ocr.web.application import *  # noqa: F401,F403
from receipt_ocr.web.application import app, run


if __name__ == "__main__":
    run()
