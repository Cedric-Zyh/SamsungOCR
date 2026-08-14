from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from receipt_ocr.paddle_ocr import recognize_line


ARTIFACT_MARKER = "/files/artifacts/"


def _resolve(url: str, root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _pure_day(text: str) -> int | None:
    compact = re.sub(r"\s+", "", str(text))
    if not re.fullmatch(r"\d{1,2}", compact):
        return None
    value = int(compact)
    return value if 1 <= value <= 31 else None


def _save_rightmost_digit_views(source: Path, destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
    array = np.asarray(rgb)
    max_channel = array.max(axis=2).astype(np.uint8)
    max_channel = np.asarray(ImageOps.autocontrast(Image.fromarray(max_channel), cutoff=1))
    ink = cv2.threshold(
        max_channel, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )[1]
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    components = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if area < 6 or height < 8:
            continue
        components.append((x, y, width, height, area))
    if not components:
        return {}
    top = min(y for _, y, _, height, _ in components)
    bottom = max(y + height for _, y, _, height, _ in components)
    ink_height = max(1, bottom - top)
    significant = [
        item for item in components
        if item[3] >= max(12, round(ink_height * 0.42))
    ]
    if not significant:
        return {}
    rightmost = max(significant, key=lambda item: item[0] + item[2])
    x, y, width, height, _ = rightmost
    pad_x = max(4, round(width * 0.35))
    pad_y = max(4, round(height * 0.20))
    box = (
        max(0, x - pad_x),
        max(0, y - pad_y),
        min(rgb.width, x + width + pad_x),
        min(rgb.height, y + height + pad_y),
    )
    original = rgb.crop(box)
    processed = Image.fromarray(max_channel).crop(box)
    target_height = 180
    target_width = max(1, round(original.width * target_height / original.height))
    canvas_width = max(320, target_width + 120)
    original_canvas = Image.new("RGB", (canvas_width, 260), "white")
    processed_canvas = Image.new("L", (canvas_width, 260), 255)
    original_resized = original.resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )
    processed_resized = processed.resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )
    offset = ((canvas_width - target_width) // 2, 40)
    original_canvas.paste(original_resized, offset)
    processed_canvas.paste(processed_resized, offset)
    original_path = destination / "far-lower-day-digit-original.png"
    processed_path = destination / "far-lower-day-digit-max-channel.png"
    original_canvas.save(original_path)
    processed_canvas.save(processed_path)
    return {"original": original_path, "processed": processed_path}


def run_probe(
    report: dict,
    samples: list[str],
    artifact_root: Path,
    generated_root: Path,
) -> dict:
    by_name = {row["filename"]: row for row in report.get("samples", [])}
    records = []
    for filename in samples:
        sample = by_name[filename]
        far_lower = next(
            (
                item for item in sample.get("date_artifact_urls") or []
                if item.get("variant") == "远下方手写日期复核区域"
            ),
            None,
        )
        if far_lower is None:
            continue
        source = _resolve(str(far_lower.get("original_url", "")), artifact_root)
        if source is None:
            continue
        views = _save_rightmost_digit_views(
            source, generated_root / Path(filename).stem
        )
        texts = {}
        values = {"mobile": set(), "server": set()}
        for view_name in ("original", "processed"):
            path = views.get(view_name)
            if path is None:
                continue
            for model in ("mobile", "server"):
                try:
                    rows = recognize_line(path, model_variant=model)
                except Exception:
                    rows = []
                texts[f"{view_name}/{model}"] = [row.text for row in rows]
                values[model].update({
                    value for row in rows
                    if (value := _pure_day(row.text)) is not None
                })
        records.append({
            "filename": filename,
            "required": sample.get("required", ""),
            "truth": sample.get("truth", ""),
            "texts": texts,
            "values": {key: sorted(value) for key, value in values.items()},
            "original_image": str(views.get("original", "")),
            "processed_image": str(views.get("processed", "")),
        })
    return {"samples": samples, "records": records}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="远下方 YYYY.M.D 手写日期的最右侧日数字裁剪探针"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sample", action="append", required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_probe(
        report,
        list(dict.fromkeys(args.sample)),
        args.artifact_root,
        args.generated_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
