from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from receipt_ocr.parser import parse_date


def _predict_text(model, path: Path) -> tuple[str, float, str]:
    try:
        results = list(model.predict(input=str(path.resolve())))
    except Exception as exc:
        return "", 0.0, str(exc)
    if not results:
        return "", 0.0, ""
    result = results[0]
    return (
        str(result.get("rec_text", "") or "").strip(),
        float(result.get("rec_score", 0.0) or 0.0),
        "",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用英文/数字 PP-OCRv5 识别固定日期数字槽"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--sample", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="en_PP-OCRv5_mobile_rec")
    args = parser.parse_args()

    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", "/Volumes/SN770/OCR")
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import TextRecognition

    model = TextRecognition(model_name=args.model, device="cpu")
    report = json.loads(args.input.read_text(encoding="utf-8"))
    by_name = {row["filename"]: row for row in report.get("samples", [])}
    records = []
    hits = 0
    for filename in list(dict.fromkeys(args.sample)):
        truth = parse_date(by_name[filename].get("truth", ""))
        if truth is None:
            continue
        stem = Path(filename).stem
        for slot in ("month_digits", "day_digits", "month_day"):
            variant_dir = args.image_root / stem / slot / "variants"
            for index, preprocessing in (
                ("02", "最大通道自动对比三倍放大"),
                ("03", "最大通道去横线三倍放大"),
            ):
                matches = sorted(variant_dir.glob(f"{index}-*.png"))
                if not matches:
                    continue
                path = matches[0]
                text, score, error = _predict_text(model, path)
                digits = "".join(re.findall(r"\d", text))
                if slot in {"month_digits", "day_digits"}:
                    expected = truth.month if slot == "month_digits" else truth.day
                    truth_hit = digits in {str(expected), f"{expected:02d}"}
                else:
                    expected = {f"{truth.month}{truth.day}", f"{truth.month:02d}{truth.day:02d}"}
                    truth_hit = digits in expected
                hits += int(truth_hit)
                records.append({
                    "filename": filename,
                    "truth": truth.isoformat(),
                    "slot": slot,
                    "preprocessing": preprocessing,
                    "model": args.model,
                    "text": text,
                    "digits": digits,
                    "confidence": round(score, 4),
                    "truth_hit": truth_hit,
                    "image": str(path.resolve()),
                    "error": error,
                })
    payload = {
        "model": args.model,
        "sample_count": len(set(args.sample)),
        "truth_hits": hits,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "model": args.model,
        "sample_count": payload["sample_count"],
        "truth_hits": hits,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
