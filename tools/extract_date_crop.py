from __future__ import annotations

import argparse
from pathlib import Path

from receipt_ocr.image_processing import save_receipt_date_crop


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
