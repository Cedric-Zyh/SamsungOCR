from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from receipt_ocr.analyzer import (
    _parse_date_slot_month_day,
    _parse_date_slot_year,
)
from receipt_ocr.paddle_ocr import recognize_line
from receipt_ocr.parser import parse_date
from receipt_ocr.vision_ocr import recognize_text as vision_recognize_text


ARTIFACT_MARKER = "/files/artifacts/"


def _resolve(url: str, root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _white_canvas(image: Image.Image) -> Image.Image:
    target_height = 180
    width = max(1, round(image.width * target_height / max(1, image.height)))
    normalized = image.resize((width, target_height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (max(720, width + 160), 320), "white")
    canvas.paste(
        normalized.convert("RGB"),
        ((canvas.width - width) // 2, (canvas.height - target_height) // 2),
    )
    # Vision is materially more stable for the small handwritten digits when
    # the standardized canvas is persisted at the same 3x resolution used by
    # ``probe_date_slots``.  The previous 720x320 probe accidentally audited a
    # lower-resolution derivative and therefore was not comparable with the
    # earlier successful Vision observations.
    return canvas.resize(
        (canvas.width * 3, canvas.height * 3), Image.Resampling.LANCZOS
    )


def _save_views(source: Path, destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
    red, green, blue = rgb.split()
    max_channel = ImageOps.autocontrast(
        ImageChops.lighter(red, ImageChops.lighter(green, blue)), cutoff=1
    )
    boxes = {
        "year": (0.0, 0.52),
        "month_day": (0.30, 0.985),
    }
    output = {}
    for name, (left, right) in boxes.items():
        box = (round(rgb.width * left), 0, round(rgb.width * right), rgb.height)
        cropped_rgb = rgb.crop(box)
        original = destination / f"{name}-original-white.png"
        processed = destination / f"{name}-max-channel-white.png"
        legacy_processed = destination / f"{name}-crop-first-max-channel-white.png"
        _white_canvas(cropped_rgb).save(original)
        _white_canvas(max_channel.crop(box)).save(processed)
        crop_red, crop_green, crop_blue = cropped_rgb.split()
        crop_max_channel = ImageOps.autocontrast(
            ImageChops.lighter(
                crop_red, ImageChops.lighter(crop_green, crop_blue)
            ),
            cutoff=1,
        )
        _white_canvas(crop_max_channel).save(legacy_processed)
        output[f"{name}_original"] = original
        output[f"{name}_processed"] = processed
        output[f"{name}_crop_first_processed"] = legacy_processed
    return output


def _paddle_years(path: Path, model_variant: str) -> tuple[list[str], set[int]]:
    try:
        rows = recognize_line(path, model_variant=model_variant)
    except Exception:
        rows = []
    texts = [row.text for row in rows]
    values = {
        year for text in texts
        if (year := _parse_date_slot_year(text)) is not None
    }
    return texts, values


def _vision_month_days(path: Path) -> tuple[list[str], set[tuple[int, int]]]:
    try:
        rows = vision_recognize_text(
            path,
            languages=("zh-Hans", "en-US"),
            min_text_height=0.001,
            fast=False,
            custom_words=(),
            language_correction=False,
        )
    except Exception:
        rows = []
    texts = [row.text for row in rows]
    values = {
        month_day for text in texts
        if (month_day := _parse_date_slot_month_day(text)) is not None
    }
    return texts, values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="审计 Vision 双预处理月日 + Paddle 双模型年份槽位候选"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--sample", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    by_name = {row["filename"]: row for row in report.get("samples", [])}
    records = []
    for filename in list(dict.fromkeys(args.sample)):
        sample = by_name[filename]
        truth = parse_date(sample.get("truth", ""))
        artifacts = sample.get("date_artifact_urls") or []
        source = _resolve(
            str(artifacts[0].get("original_url", "")) if artifacts else "",
            args.artifact_root,
        )
        if source is None:
            records.append({"filename": filename, "error": "缺少紧凑日期行"})
            continue
        views = _save_views(
            source, args.generated_root / Path(filename).stem
        )
        mobile_texts, mobile_years = _paddle_years(
            views["year_processed"], "mobile"
        )
        server_texts, server_years = _paddle_years(
            views["year_processed"], "server"
        )
        original_texts, original_month_days = _vision_month_days(
            views["month_day_original"]
        )
        processed_texts, processed_month_days = _vision_month_days(
            views["month_day_processed"]
        )
        crop_first_texts, crop_first_month_days = _vision_month_days(
            views["month_day_crop_first_processed"]
        )
        common_years = mobile_years & server_years
        common_month_days = original_month_days & processed_month_days
        candidates = []
        if len(common_years) == len(common_month_days) == 1:
            year = next(iter(common_years))
            month, day = next(iter(common_month_days))
            try:
                candidates.append(date(year, month, day).isoformat())
            except ValueError:
                pass
        low_confidence_candidates = []
        if len(common_years) == len(crop_first_month_days) == 1:
            year = next(iter(common_years))
            month, day = next(iter(crop_first_month_days))
            try:
                low_confidence_candidates.append(
                    date(year, month, day).isoformat()
                )
            except ValueError:
                pass
        records.append({
            "filename": filename,
            "truth": truth.isoformat() if truth else "",
            "paddle_mobile_year_texts": mobile_texts,
            "paddle_server_year_texts": server_texts,
            "common_years": sorted(common_years),
            "vision_original_month_day_texts": original_texts,
            "vision_processed_month_day_texts": processed_texts,
            "vision_crop_first_month_day_texts": crop_first_texts,
            "common_month_days": [
                f"{month:02d}-{day:02d}"
                for month, day in sorted(common_month_days)
            ],
            "crop_first_month_days": [
                f"{month:02d}-{day:02d}"
                for month, day in sorted(crop_first_month_days)
            ],
            "candidates": candidates,
            "low_confidence_candidates": low_confidence_candidates,
            "truth_hit": bool(truth and truth.isoformat() in candidates),
            "low_confidence_truth_hit": bool(
                truth and truth.isoformat() in low_confidence_candidates
            ),
            "false_candidates": [
                value for value in candidates
                if not truth or value != truth.isoformat()
            ],
            "low_confidence_false_candidates": [
                value for value in low_confidence_candidates
                if not truth or value != truth.isoformat()
            ],
            "images": {key: str(value.resolve()) for key, value in views.items()},
            "error": "",
        })
    payload = {
        "sample_count": len(set(args.sample)),
        "candidate_count": sum(bool(row.get("candidates")) for row in records),
        "truth_hits": sum(bool(row.get("truth_hit")) for row in records),
        "false_candidate_count": sum(
            len(row.get("false_candidates") or []) for row in records
        ),
        "low_confidence_candidate_count": sum(
            bool(row.get("low_confidence_candidates")) for row in records
        ),
        "low_confidence_truth_hits": sum(
            bool(row.get("low_confidence_truth_hit")) for row in records
        ),
        "low_confidence_false_candidate_count": sum(
            len(row.get("low_confidence_false_candidates") or [])
            for row in records
        ),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        key: payload[key]
        for key in (
            "sample_count", "candidate_count", "truth_hits",
            "false_candidate_count", "low_confidence_candidate_count",
            "low_confidence_truth_hits",
            "low_confidence_false_candidate_count",
        )
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
