from __future__ import annotations

# Keep direct CLI execution independent of the current working directory.
import sys as _sys
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
from pathlib import Path

from receipt_ocr.imaging.processing import save_receipt_date_crop


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--anchor", type=float, default=0.472)
    parser.add_argument("--output", default="tmp/date-crop.png")
    args = parser.parse_args()
    save_receipt_date_crop(Path(args.image), Path(args.output), args.anchor)
    print(Path(args.output).resolve())


if __name__ == "__main__":
    main()
