from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from receipt_ocr.paddle_ocr import recognize_line
from receipt_ocr.parser import parse_date, parse_receipt_date


ARTIFACT_MARKER = "/files/artifacts/"


def _resolve(url: str, root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _save_variants(source: Path, destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
    gray = ImageOps.grayscale(rgb)
    rgb_array = np.asarray(rgb)
    # A red/blue stamp is bright in at least one RGB channel, while black
    # handwriting stays dark in all three.  The per-pixel maximum therefore
    # suppresses colored ink without relying on a saturation cutoff that can
    # erase black strokes where pen and stamp overlap.
    max_channel = Image.fromarray(rgb_array.max(axis=2).astype(np.uint8))
    max_channel_contrast = ImageOps.autocontrast(max_channel, cutoff=1)
    scale = 3
    size = (rgb.width * scale, rgb.height * scale)
    images: dict[str, Image.Image] = {
        "原图三倍放大": rgb.resize(size, Image.Resampling.LANCZOS),
        "灰度自动对比三倍放大": ImageOps.autocontrast(gray, cutoff=1).resize(
            size, Image.Resampling.LANCZOS
        ),
        "最大通道去彩色三倍放大": max_channel_contrast.resize(
            size, Image.Resampling.LANCZOS
        ),
    }
    array = np.asarray(gray)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(array)
    otsu = cv2.threshold(
        array, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )[1]
    adaptive = cv2.adaptiveThreshold(
        array, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 13,
    )
    max_otsu = cv2.threshold(
        np.asarray(max_channel_contrast), 0, 255,
        cv2.THRESH_BINARY | cv2.THRESH_OTSU,
    )[1]
    for label, value in (
        ("CLAHE三倍放大", clahe),
        ("Otsu二值三倍放大", otsu),
        ("自适应二值三倍放大", adaptive),
        ("最大通道Otsu三倍放大", max_otsu),
    ):
        images[label] = Image.fromarray(value).resize(
            size, Image.Resampling.NEAREST
        )
    output = {}
    for index, (label, image) in enumerate(images.items()):
        path = destination / f"{index:02d}-{label}.png"
        image.save(path)
        output[label] = path
    return output


def run_probe(
    report: dict,
    samples: list[str],
    artifact_root: Path,
    generated_root: Path,
) -> dict:
    by_name = {row["filename"]: row for row in report.get("samples", [])}
    records = []
    hit_counts: Counter[str] = Counter()
    for filename in samples:
        row = by_name[filename]
        required = parse_date(row.get("required", ""))
        truth = parse_date(row.get("truth", ""))
        for artifact in (row.get("date_artifact_urls") or [])[:2]:
            variant_label = str(artifact.get("variant", ""))
            sources = (
                ("日期行原图", artifact.get("original_url", "")),
                ("日期行去印章色", artifact.get("color_clean_url", "")),
                ("日期行去表格线", artifact.get("table_clean_url", "")),
            )
            for source_label, source_url in sources:
                source = _resolve(str(source_url), artifact_root)
                if source is None:
                    continue
                generated = _save_variants(
                    source,
                    generated_root / Path(filename).stem / variant_label / source_label,
                )
                for preprocessing, path in generated.items():
                    for engine in ("mobile", "server"):
                        try:
                            observations = recognize_line(path, model_variant=engine)
                            texts = [item.text for item in observations]
                        except Exception as error:
                            texts = []
                            records.append({
                                "filename": filename,
                                "geometry": variant_label,
                                "source": source_label,
                                "preprocessing": preprocessing,
                                "engine": engine,
                                "texts": [],
                                "parsed": [],
                                "truth_hit": False,
                                "error": str(error),
                                "image": str(path.resolve()),
                            })
                            continue
                        parsed = []
                        for text in texts:
                            value = parse_date(text) or parse_receipt_date(text, required)
                            if value is not None:
                                parsed.append(value.isoformat())
                        truth_hit = bool(truth and truth.isoformat() in parsed)
                        if truth_hit:
                            hit_counts[
                                f"{engine}/{source_label}/{preprocessing}"
                            ] += 1
                        records.append({
                            "filename": filename,
                            "geometry": variant_label,
                            "source": source_label,
                            "preprocessing": preprocessing,
                            "engine": engine,
                            "texts": texts,
                            "parsed": parsed,
                            "truth_hit": truth_hit,
                            "error": "",
                            "image": str(path.resolve()),
                        })
    return {
        "samples": samples,
        "sample_count": len(samples),
        "hit_counts": dict(hit_counts.most_common()),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="日期行预处理 Mobile/Server 对照")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sample", action="append", required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_probe(
        report, list(dict.fromkeys(args.sample)),
        args.artifact_root, args.generated_root,
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
