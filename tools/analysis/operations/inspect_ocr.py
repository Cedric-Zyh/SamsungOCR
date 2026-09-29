from __future__ import annotations

# Keep direct CLI execution independent of the current working directory.
import sys as _sys
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

import argparse
import json

from receipt_ocr.vision_ocr import recognize_text


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 Vision OCR 原始结果")
    parser.add_argument("image")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--contains", default="")
    parser.add_argument("--min-y", type=float, default=0.0)
    parser.add_argument("--max-y", type=float, default=1.0)
    parser.add_argument("--languages", default="zh-Hans,en-US")
    args = parser.parse_args()

    rows = recognize_text(args.image, fast=args.fast, languages=args.languages.split(","))
    rows = [
        row for row in rows
        if args.min_y <= row.y <= args.max_y
        and (not args.contains or args.contains in row.text)
    ]
    print(json.dumps([row.to_dict() for row in rows], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
