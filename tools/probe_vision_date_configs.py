from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from receipt_ocr.parser import parse_date
from receipt_ocr.vision_ocr import recognize_text as vision_recognize_text


ARTIFACT_MARKER = "/files/artifacts/"
CONFIGS = {
    "中英_关闭纠错": {
        "languages": ("zh-Hans", "en-US"),
        "language_correction": False,
    },
    "中英_开启纠错": {
        "languages": ("zh-Hans", "en-US"),
        "language_correction": True,
    },
    "仅中文_关闭纠错": {
        "languages": ("zh-Hans",),
        "language_correction": False,
    },
    "仅英文_关闭纠错": {
        "languages": ("en-US",),
        "language_correction": False,
    },
}


def _resolve(url: str, root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _white_canvas(image: Image.Image) -> Image.Image:
    target_height = 180
    width = max(
        1, round(image.width * target_height / max(1, image.height))
    )
    normalized = image.resize(
        (width, target_height), Image.Resampling.LANCZOS
    ).convert("RGB")
    canvas = Image.new("RGB", (max(720, width + 160), 320), "white")
    canvas.paste(
        normalized,
        ((canvas.width - width) // 2, (canvas.height - target_height) // 2),
    )
    return canvas.resize(
        (canvas.width * 3, canvas.height * 3), Image.Resampling.LANCZOS
    )


def _save_views(
    original: Path,
    color_clean: Path | None,
    table_clean: Path | None,
    destination: Path,
) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(original) as opened:
        rgb = opened.convert("RGB")
    red, green, blue = rgb.split()
    max_channel = ImageOps.autocontrast(
        ImageChops.lighter(red, ImageChops.lighter(green, blue)), cutoff=1
    )
    images: dict[str, Image.Image] = {
        "原日期行三倍放大": rgb.resize(
            (rgb.width * 3, rgb.height * 3), Image.Resampling.LANCZOS
        ),
        "原日期行白底标准化": _white_canvas(rgb),
        "灰度自动对比三倍放大": ImageOps.autocontrast(
            ImageOps.grayscale(rgb), cutoff=1
        ).resize(
            (rgb.width * 3, rgb.height * 3), Image.Resampling.LANCZOS
        ).convert("RGB"),
        "最大通道白底标准化": _white_canvas(max_channel),
    }
    for label, path in (
        ("去章色日期行三倍放大", color_clean),
        ("去表格线日期行三倍放大", table_clean),
    ):
        if path is None:
            continue
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        images[label] = image.resize(
            (image.width * 3, image.height * 3), Image.Resampling.LANCZOS
        )
    output = {}
    for index, (label, image) in enumerate(images.items()):
        path = destination / f"{index:02d}-{label}.png"
        image.save(path)
        output[label] = path
    return output


def _recognize(path: Path, config: dict) -> tuple[list[str], list[str], str]:
    try:
        rows = vision_recognize_text(
            path,
            languages=config["languages"],
            min_text_height=0.001,
            fast=False,
            custom_words=(),
            language_correction=config["language_correction"],
        )
        texts = [row.text for row in rows]
        dates = sorted({
            parsed.isoformat()
            for text in texts
            if (parsed := parse_date(text)) is not None
        })
        return texts, dates, ""
    except Exception as exc:
        return [], [], str(exc)


def run_probe(
    report: dict,
    samples: list[str],
    artifact_root: Path,
    generated_root: Path,
) -> dict:
    by_name = {row["filename"]: row for row in report.get("samples", [])}
    records = []
    for filename in list(dict.fromkeys(samples)):
        sample = by_name[filename]
        artifacts = sample.get("date_artifact_urls") or []
        artifact = artifacts[0] if artifacts else {}
        original = _resolve(str(artifact.get("original_url", "")), artifact_root)
        if original is None:
            records.append({
                "filename": filename,
                "truth": str(sample.get("truth", "")),
                "error": "缺少紧凑日期行",
                "observations": [],
            })
            continue
        views = _save_views(
            original,
            _resolve(str(artifact.get("color_clean_url", "")), artifact_root),
            _resolve(str(artifact.get("table_clean_url", "")), artifact_root),
            generated_root / Path(filename).stem,
        )
        observations = []
        evidence_by_date: dict[str, list[dict]] = defaultdict(list)
        for view, path in views.items():
            for config_name, config in CONFIGS.items():
                texts, dates, error = _recognize(path, config)
                item = {
                    "view": view,
                    "config": config_name,
                    "texts": texts,
                    "strict_dates": dates,
                    "image": str(path.resolve()),
                    "error": error,
                }
                observations.append(item)
                for value in dates:
                    evidence_by_date[value].append({
                        "view": view,
                        "config": config_name,
                        "texts": texts,
                    })
        candidates = []
        for value, evidence in sorted(evidence_by_date.items()):
            views_for_value = {item["view"] for item in evidence}
            configs_for_value = {item["config"] for item in evidence}
            same_view_multi_config = any(
                len({
                    item["config"] for item in evidence
                    if item["view"] == view
                }) >= 2
                for view in views_for_value
            )
            candidates.append({
                "value": value,
                "view_count": len(views_for_value),
                "config_count": len(configs_for_value),
                "same_view_multi_config": same_view_multi_config,
                "cross_view": len(views_for_value) >= 2,
                "strict_consensus": (
                    same_view_multi_config and len(views_for_value) >= 2
                ),
                "evidence": evidence,
            })
        truth = str(sample.get("truth", ""))
        strict_values = [
            item["value"] for item in candidates if item["strict_consensus"]
        ]
        records.append({
            "filename": filename,
            "truth": truth,
            "candidates": candidates,
            "strict_consensus_values": strict_values,
            "strict_truth_hit": bool(truth and truth in strict_values),
            "strict_false_values": [
                value for value in strict_values if not truth or value != truth
            ],
            "views": {key: str(value.resolve()) for key, value in views.items()},
            "observations": observations,
            "error": "",
        })
    return {
        "sample_count": len(set(samples)),
        "configurations": CONFIGS,
        "strict_candidate_count": sum(
            bool(row.get("strict_consensus_values")) for row in records
        ),
        "strict_truth_hits": sum(
            bool(row.get("strict_truth_hit")) for row in records
        ),
        "strict_false_candidate_count": sum(
            len(row.get("strict_false_values") or []) for row in records
        ),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="审计 Vision 多语言配置与日期行预处理的一致性"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--sample", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    payload = run_probe(
        report,
        args.sample,
        args.artifact_root,
        args.generated_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        key: payload[key]
        for key in (
            "sample_count", "strict_candidate_count", "strict_truth_hits",
            "strict_false_candidate_count",
        )
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
