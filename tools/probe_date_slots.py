from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from receipt_ocr.paddle_ocr import recognize_line, recognize_text
from receipt_ocr.parser import parse_date


ARTIFACT_MARKER = "/files/artifacts/"
SLOTS = {
    # Keep the following printed unit glyph in each crop.  ``年/月/日`` gives
    # the recognizer useful context while separating the three handwritten
    # components that whole-line OCR frequently merges with the form border.
    "year_full": (0.0, 0.52),
    "year_suffix": (0.12, 0.47),
    "month": (0.36, 0.74),
    "day": (0.50, 0.985),
    "day_digits": (0.54, 0.82),
    # Some writers put month and day so close together that either individual
    # slot clips a leading digit.  This wider derivative is accepted only when
    # the OCR text itself contains both components and a visible separator.
    "month_day": (0.30, 0.985),
}


def _resolve(url: str, root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _slot_variants(
    source: Path | Image.Image, destination: Path
) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    if isinstance(source, Image.Image):
        rgb = source.convert("RGB")
    else:
        with Image.open(source) as opened:
            rgb = opened.convert("RGB")
    rgb_array = np.asarray(rgb)
    max_channel = Image.fromarray(rgb_array.max(axis=2).astype(np.uint8))
    variants = {
        "原槽位三倍放大": rgb,
        "灰度自动对比三倍放大": ImageOps.autocontrast(
            ImageOps.grayscale(rgb), cutoff=1
        ),
        "最大通道自动对比三倍放大": ImageOps.autocontrast(
            max_channel, cutoff=1
        ),
    }
    max_array = np.asarray(variants["最大通道自动对比三倍放大"])
    ink = cv2.threshold(
        max_array, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
    )[1]
    horizontal = cv2.morphologyEx(
        ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(18, ink.shape[1] // 3), 2)
        ),
    )
    line_clean = cv2.bitwise_not(cv2.subtract(ink, horizontal))
    variants["最大通道去横线三倍放大"] = Image.fromarray(line_clean)
    normalized_height = 180
    normalized_width = max(
        1, round(max_channel.width * normalized_height / max(1, max_channel.height))
    )
    normalized = ImageOps.autocontrast(max_channel, cutoff=1).resize(
        (normalized_width, normalized_height), Image.Resampling.LANCZOS
    )
    canvas = Image.new(
        "L", (max(720, normalized_width + 160), 320), color=255
    )
    canvas.paste(
        normalized,
        ((canvas.width - normalized.width) // 2, (canvas.height - normalized.height) // 2),
    )
    # macOS Vision's CGImage bridge rejects this synthetic image when it is
    # saved as single-channel ``L`` even though Paddle accepts it. Keep the
    # visible pixels identical but persist a conventional RGB PNG so the same
    # artifact can be compared across both engines.
    variants["最大通道白边标准化"] = canvas.convert("RGB")
    output = {}
    for index, (label, image) in enumerate(variants.items()):
        target = destination / f"{index:02d}-{label}.png"
        image.resize(
            (max(180, image.width * 3), max(96, image.height * 3)),
            Image.Resampling.LANCZOS,
        ).save(target)
        output[label] = target
    return output


def _digits(text: str) -> str:
    normalized = text.replace("O", "0").replace("o", "0")
    return "".join(re.findall(r"\d", normalized))


def _component_matches(slot: str, text: str, truth) -> bool:
    digits = _digits(text)
    if slot == "year_full":
        before_year = re.search(r"(20\d{2})年", text.replace(" ", ""))
        return bool(before_year and int(before_year.group(1)) == truth.year)
    if slot == "year_suffix":
        before_year = re.search(r"(\d{2,4})年", text)
        return bool(
            before_year
            and before_year.group(1).endswith(f"{truth.year % 100:02d}")
        )
    if slot == "month_day":
        compact = text.replace(" ", "")
        match = re.search(r"(\d{1,2})(?:[./-]|月)(\d{1,2})(?:日)?", compact)
        return bool(
            match
            and int(match.group(1)) == truth.month
            and int(match.group(2)) == truth.day
        )
    expected = truth.month if slot == "month" else truth.day
    unit = "月" if slot == "month" else "日"
    before_unit = re.search(rf"(\d{{1,2}}){unit}", text)
    if before_unit:
        digits = before_unit.group(1)
    return digits in {str(expected), f"{expected:02d}"}


def run_probe(
    report: dict,
    samples: list[str],
    artifact_root: Path,
    generated_root: Path,
    with_detection: bool = False,
) -> dict:
    by_name = {row["filename"]: row for row in report.get("samples", [])}
    records = []
    hit_counts: Counter[str] = Counter()
    for filename in samples:
        sample = by_name[filename]
        truth = parse_date(sample.get("truth", ""))
        if truth is None:
            continue
        artifacts = sample.get("date_artifact_urls") or []
        if not artifacts:
            continue
        source = _resolve(str(artifacts[0].get("original_url", "")), artifact_root)
        if source is None:
            continue
        with Image.open(source) as opened:
            line = opened.convert("RGB")
        for slot, (left, right) in SLOTS.items():
            crop = line.crop((
                round(line.width * left), 0,
                round(line.width * right), line.height,
            ))
            crop_path = generated_root / Path(filename).stem / slot / "slot.png"
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            crop.save(crop_path)
            for preprocessing, path in _slot_variants(
                crop, crop_path.parent / "variants"
            ).items():
                for engine in ("mobile", "server"):
                    error = ""
                    try:
                        observations = recognize_line(path, model_variant=engine)
                        texts = [row.text for row in observations]
                    except Exception as exc:
                        texts = []
                        error = str(exc)
                    matches = [
                        text for text in texts
                        if _component_matches(slot, text, truth)
                    ]
                    if matches:
                        hit_counts[f"{slot}/{engine}/{preprocessing}"] += 1
                    records.append({
                        "filename": filename,
                        "truth": truth.isoformat(),
                        "slot": slot,
                        "preprocessing": preprocessing,
                        "engine": engine,
                        "method": "line_recognition",
                        "texts": texts,
                        "digits": [_digits(text) for text in texts],
                        "truth_matches": matches,
                        "image": str(path.resolve()),
                        "error": error,
                    })
                    if (
                        with_detection
                        and slot in {"year_full", "month_day"}
                        and preprocessing == "最大通道自动对比三倍放大"
                    ):
                        detection_error = ""
                        try:
                            detected = recognize_text(
                                path,
                                model_variant=engine,
                                min_text_height=0.001,
                            )
                            detected_texts = [row.text for row in detected]
                        except Exception as exc:
                            detected_texts = []
                            detection_error = str(exc)
                        detected_matches = [
                            text for text in detected_texts
                            if _component_matches(slot, text, truth)
                        ]
                        if detected_matches:
                            hit_counts[f"{slot}/{engine}/检测识别"] += 1
                        records.append({
                            "filename": filename,
                            "truth": truth.isoformat(),
                            "slot": slot,
                            "preprocessing": preprocessing,
                            "engine": engine,
                            "method": "detection_recognition",
                            "texts": detected_texts,
                            "digits": [_digits(text) for text in detected_texts],
                            "truth_matches": detected_matches,
                            "image": str(path.resolve()),
                            "error": detection_error,
                        })
    return {
        "samples": samples,
        "sample_count": len(samples),
        "slots": SLOTS,
        "hit_counts": dict(hit_counts.most_common()),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="固定日期模板按年后两位/月/日槽位执行 Mobile/Server 探针"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sample", action="append", required=True)
    parser.add_argument(
        "--artifact-root", type=Path, default=Path("storage/artifacts")
    )
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-detection", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_probe(
        report,
        list(dict.fromkeys(args.sample)),
        args.artifact_root,
        args.generated_root,
        with_detection=args.with_detection,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "sample_count": result["sample_count"],
        "hit_counts": result["hit_counts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
