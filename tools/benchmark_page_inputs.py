"""Read-only page OCR input experiments against local receipt ground truth.

Run with python -m tools.benchmark_page_inputs. Never initializes the web app,
opens the business database, changes model defaults, or calls a remote API.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
from statistics import median
import time

from PIL import Image, ImageDraw

from receipt_ocr.document_types import classify_document
from receipt_ocr.document_layout import _find_signature_requirement_row
from receipt_ocr.parser import (
    parse_fields,
    parse_product_table,
    normalize_text,
    estimate_field_confidences,
    LOW_CONFIDENCE_THRESHOLD,
)

VARIANTS = {
    "original": {},
    "image2048": {"image_max_side": 2048},
    "detect2048": {"detector_max_side": 2048},
    "detect2560": {"detector_max_side": 2560},
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def select_samples(data, count=12):
    """Deterministic strata; sharpness and color are selection proxies only."""
    import cv2
    import numpy as np

    truth = json.loads((data / "ground_truth.json").read_text())
    document_truth = json.loads((data / "document_ground_truth.json").read_text())[
        "documents"
    ]
    pool = []
    for name in sorted(set(truth) | set(document_truth)):
        path = data / name
        if not path.is_file():
            continue
        with Image.open(path) as original:
            size = original.size
            view = original.convert("RGB")
            view.thumbnail((800, 800))
            pixels = np.asarray(view)
        gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        color = float(
            ((pixels.max(axis=2).astype(float) - pixels.min(axis=2)) > 65).mean()
        )
        pool.append(
            dict(
                filename=name,
                size=size,
                sharpness=round(sharpness, 3),
                color_ratio=round(color, 6),
                expected_type=document_truth.get(name, {}).get("type", "receipt"),
                product_rows=len(truth.get(name, {}).get("product_rows", [])),
                expected_row_count=document_truth.get(name, {}).get(
                    "product_row_count"
                ),
            )
        )
    picked = []

    def add(candidates, category, amount):
        used = {x["filename"] for x in picked}
        for item in candidates:
            if item["filename"] in used:
                continue
            picked.append({**item, "category": category})
            used.add(item["filename"])
            amount -= 1
            if not amount:
                break

    add(
        [p for p in pool if p["expected_type"] == "product_continuation"], "商品续页", 1
    )
    add(
        [
            p
            for p in pool
            if p.get("expected_row_count") and p["expected_type"] == "receipt"
        ],
        "密集分页首页",
        1,
    )
    add(
        [p for p in pool if p["expected_type"] == "warehouse_authorization"],
        "委托书",
        1,
    )
    receipts = [p for p in pool if p["expected_type"] == "receipt"]
    add(sorted(receipts, key=lambda p: -p["product_rows"]), "商品行数较多", 2)
    add(sorted(receipts, key=lambda p: max(p["size"])), "较低原图分辨率", 2)
    add(sorted(receipts, key=lambda p: p["sharpness"]), "低边缘清晰度代理", 2)
    add(sorted(receipts, key=lambda p: -p["color_ratio"]), "彩色墨迹较多", 2)
    add(
        sorted(receipts, key=lambda p: -p["sharpness"]),
        "高边缘清晰度代理",
        max(1, count - len(picked)),
    )
    return picked[:count]


def contact_sheet(data, samples, destination):
    sheet = Image.new("RGB", (1200, ((len(samples) + 3) // 4) * 480), "white")
    draw = ImageDraw.Draw(sheet)
    for index, sample in enumerate(samples):
        with Image.open(data / sample["filename"]) as im:
            view = im.convert("RGB")
            view.thumbnail((290, 445))
        x, y = (index % 4) * 300, (index // 4) * 480
        sheet.paste(view, (x + (300 - view.width) // 2, y + 25))
        draw.text((x + 5, y + 5), f"{index+1}. {sample['filename']}", fill="black")
    sheet.save(destination)


def evidence(rows, truth, expected_type, expected_row_count=None):
    fields = parse_fields(rows)
    table = parse_product_table(rows)
    products = {
        normalize_text(str(r.get("values", {}).get("行号", ""))): r.get("values", {})
        for r in table.get("rows", [])
    }
    cells = {}
    for name, value in truth.get("fields", {}).items():
        cells["field:" + name] = normalize_text(
            str(fields.get(name, ""))
        ) == normalize_text(str(value))
    for r in truth.get("product_rows", []):
        number = normalize_text(str(r.get("行号", "")))
        actual = products.get(number, {})
        for name, value in r.items():
            cells[f"product:{number}:{name}"] = normalize_text(
                str(actual.get(name, ""))
            ) == normalize_text(str(value))
    if truth.get("product_rows"):
        cells["product_row_count"] = len(table.get("rows", [])) == len(
            truth["product_rows"]
        )
    metadata = estimate_field_confidences(fields, rows, "")
    for name in truth.get("fields", {}):
        cells["confidence:" + name] = (
            cells["field:" + name]
            and metadata.get(name, {}).get("confidence", 0) >= LOW_CONFIDENCE_THRESHOLD
        )
    doc = classify_document(rows)
    cells["document_type"] = doc["type"] == expected_type
    if expected_row_count is not None:
        cells["document_row_count"] = len(table.get("rows", [])) == expected_row_count
    footer = _find_signature_requirement_row(rows)
    return dict(
        fields=fields,
        product_table=table,
        checks=cells,
        document_type=doc,
        footer_y=footer.y if footer else None,
        text_count=len(rows),
    )


def summarize(runs):
    output = {}
    base = {
        r["filename"]: r
        for r in runs
        if r["variant"] == "original" and "error" not in r
    }
    for variant in dict.fromkeys(r["variant"] for r in runs):
        selected = [r for r in runs if r["variant"] == variant]
        correct, total = Counter(), Counter()
        regressions, improvements, anchors, pairs = [], [], [], []
        for run in selected:
            if "error" in run:
                continue
            for key, good in run["evidence"]["checks"].items():
                group = key.split(":")[0]
                total[group] += 1
                correct[group] += bool(good)
            original = base.get(run["filename"])
            if not original:
                continue
            pairs.append((original["seconds"], run["seconds"]))
            for key, previous in original["evidence"]["checks"].items():
                current = run["evidence"]["checks"].get(key, False)
                if previous and not current:
                    regressions.append({"filename": run["filename"], "check": key})
                if current and not previous:
                    improvements.append({"filename": run["filename"], "check": key})
            y1, y2 = original["evidence"]["footer_y"], run["evidence"]["footer_y"]
            if (y1 is None) != (y2 is None) or (
                y1 is not None and abs(y1 - y2) > 0.015
            ):
                anchors.append(run["filename"])
        output[variant] = dict(
            samples=len(selected),
            failures=sum("error" in r for r in selected),
            median_seconds=round(median([r["seconds"] for r in selected]), 3),
            paired_speedup=(
                round(sum(a for a, b in pairs) / sum(b for a, b in pairs), 3)
                if pairs
                else None
            ),
            correct=dict(correct),
            total=dict(total),
            regressions=regressions,
            improvements=improvements,
            footer_anchor_changes=anchors,
            passes_quality_gate=bool(pairs)
            and len(pairs) == len(selected) == len(base)
            and not regressions
            and not anchors
            and not any("error" in r for r in selected),
        )
    return output


class PredictionOptions:
    def __init__(self, pipeline, options):
        self.pipeline, self.options = pipeline, options

    def predict(self, **kwargs):
        return self.pipeline.predict(**kwargs, **self.options)


class TimedPredictor:
    """Measure native detector/recognizer generators without changing evidence."""

    def __init__(self, predictor, totals, key):
        self.predictor, self.totals, self.key = predictor, totals, key

    def __getattr__(self, name):
        return getattr(self.predictor, name)

    def __call__(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            yield from self.predictor(*args, **kwargs)
        finally:
            self.totals[self.key] = (
                self.totals.get(self.key, 0) + time.perf_counter() - started
            )


def run(data, samples, variants, output, *, profile=False):
    from receipt_ocr import paddle_ocr

    truth = json.loads((data / "ground_truth.json").read_text())
    stamp = dict(
        platform=platform.platform(),
        python=platform.python_version(),
        packages={
            name: importlib.metadata.version(name)
            for name in ("paddleocr", "paddlex", "paddlepaddle")
        },
        variants={name: VARIANTS[name] for name in variants},
        truth_sha256=hashlib.sha256(
            (data / "ground_truth.json").read_bytes()
        ).hexdigest(),
        notes=[
            "Only page OCR is timed; parsing and truth comparison follow inference.",
            "Same warmed Mobile model; variant order rotates by sample.",
            "Proxy selection scores are not manual image-quality grades.",
            "Raw OCR parsing is tested conservatively before contextual repairs.",
        ],
    )
    write_json(output / "environment.json", stamp)
    print("Loading and warming Mobile OCR", flush=True)
    pipeline = paddle_ocr._pipeline("mobile")
    paddle_ocr.recognize_text(data / samples[0]["filename"])
    phases = {}
    if profile:
        internal = pipeline.paddlex_pipeline._pipeline
        for attribute, key in (
            ("text_det_model", "detection"),
            ("text_rec_model", "recognition"),
        ):
            setattr(
                internal,
                attribute,
                TimedPredictor(getattr(internal, attribute), phases, key),
            )
    runs = []
    for index, sample in enumerate(samples):
        source = data / sample["filename"]
        rotated = variants[index % len(variants) :] + variants[: index % len(variants)]
        for variant in rotated:
            options = VARIANTS[variant]
            inference_source = source
            phases.clear()
            started = time.perf_counter()
            try:
                if options.get("image_max_side"):
                    inference_source = output / "inputs" / sample["filename"]
                    inference_source.parent.mkdir(exist_ok=True)
                    with Image.open(source) as im:
                        scaled = im.convert("RGB")
                        scaled.thumbnail(
                            (options["image_max_side"],) * 2, Image.Resampling.LANCZOS
                        )
                        scaled.save(inference_source, quality=94)
                kwargs = {}
                if options.get("detector_max_side"):
                    kwargs = dict(
                        text_det_limit_side_len=options["detector_max_side"],
                        text_det_limit_type="max",
                    )
                paddle_ocr._PIPELINES["mobile"] = PredictionOptions(pipeline, kwargs)
                rows = paddle_ocr.recognize_text(inference_source)
                seconds = time.perf_counter() - started
                result = dict(
                    filename=sample["filename"],
                    variant=variant,
                    seconds=round(seconds, 4),
                    evidence=evidence(
                        rows,
                        truth.get(sample["filename"], {}),
                        sample["expected_type"],
                        sample["expected_row_count"],
                    ),
                )
                folder = output / "observations" / variant
                folder.mkdir(parents=True, exist_ok=True)
                write_json(
                    folder / (sample["filename"] + ".json"), [asdict(r) for r in rows]
                )
            except Exception as exc:
                result = dict(
                    filename=sample["filename"],
                    variant=variant,
                    seconds=round(time.perf_counter() - started, 4),
                    error=str(exc),
                )
            finally:
                paddle_ocr._PIPELINES["mobile"] = pipeline
            if profile:
                result["phases"] = {k: round(v, 4) for k, v in phases.items()}
            runs.append(result)
            write_json(output / "runs.json", runs)
            write_json(output / "summary.json", summarize(runs))
            print(
                json.dumps(
                    {k: result[k] for k in ("filename", "variant", "seconds")}
                    | {
                        "completed": len(runs),
                        "total": len(samples) * len(variants),
                        "error": result.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return summarize(runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("数据"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Measure detector and recognizer separately using the installed PaddleX pipeline",
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=VARIANTS,
        default=["original", "image2048", "detect2048", "detect2560"],
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    samples = (
        json.loads(args.manifest.read_text())
        if args.manifest
        else select_samples(args.data, args.count)
    )
    if not samples:
        raise ValueError("没有可用样本")
    if len({sample["filename"] for sample in samples}) != len(samples):
        raise ValueError("样本清单包含重复文件")
    for sample in samples:
        digest = hashlib.sha256(
            (args.data / sample["filename"]).read_bytes()
        ).hexdigest()
        if sample.get("sha256") and sample["sha256"] != digest:
            raise ValueError(f"样本内容与清单不一致：{sample['filename']}")
        sample["sha256"] = digest
    write_json(args.output / "manifest.json", samples)
    contact_sheet(args.data, samples, args.output / "samples.jpg")
    if not args.select_only:
        print(
            json.dumps(
                run(
                    args.data, samples, args.variants, args.output, profile=args.profile
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
