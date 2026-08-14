from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from receipt_ocr.analyzer import (
    _parse_compact_full_date_audit_candidate,
    _parse_date_slot_year,
)
from receipt_ocr.paddle_ocr import recognize_line
from receipt_ocr.parser import parse_date
from tools.analyze_date_gaps import _engine, _latest_original_results, _observations


ARTIFACT_MARKER = "/files/artifacts/"
MONTH_DIGIT_WINDOW = (0.405, 0.515)


def _resolve_artifact(url: str, root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    path = root / url.split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _tight_artifact(result: dict) -> dict | None:
    for artifact in (result.get("processing_artifacts") or {}).get("date") or []:
        if (
            artifact.get("crop_key") == "tight"
            or artifact.get("variant") == "紧凑区域"
        ) and not artifact.get("audit_only"):
            return artifact
    return None


def _slot_years_by_model(artifact: dict) -> dict[str, set[int]]:
    values = {"mobile": set(), "server": set()}
    for variant in artifact.get("date_slot_ocr_variants") or []:
        model = str(variant.get("model", ""))
        if model not in values:
            continue
        if variant.get("preprocessing") != "最大通道去彩色":
            continue
        for text in variant.get("ocr_texts") or []:
            parsed = _parse_date_slot_year(str(text))
            if parsed is not None:
                values[model].add(parsed)
    return values


def _explicit_components(artifact: dict) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    months = {"mobile": set(), "server": set()}
    days = {"mobile": set(), "server": set()}
    default_backend = str(artifact.get("date_line_ocr_backend", ""))
    for variant in artifact.get("date_line_ocr_variants") or []:
        preprocessing = str(variant.get("preprocessing", ""))
        backend = (
            "PaddleOCR Server"
            if "Server" in preprocessing or "大模型" in preprocessing
            else default_backend
        )
        engine = _engine(backend)
        if engine not in months:
            continue
        for text in variant.get("ocr_texts") or []:
            compact = re.sub(r"\s+", "", str(text))
            for value in re.findall(r"(?<!\d)(\d{1,2})月", compact):
                month = int(value)
                if 1 <= month <= 12:
                    months[engine].add(month)
            for value in re.findall(r"(?<!\d)(\d{1,2})日", compact):
                day = int(value)
                if 1 <= day <= 31:
                    days[engine].add(day)
    return months, days


def _literal_dates(result: dict) -> set[date]:
    values: set[date] = set()
    for observation in _observations(result):
        text = str(observation.get("text", ""))
        parsed = parse_date(text) or _parse_compact_full_date_audit_candidate(text)
        if parsed is not None:
            values.add(parsed)
    return values


def _month_digit_views(
    source: Path, destination: Path
) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
    left, right = MONTH_DIGIT_WINDOW
    box = (
        round(rgb.width * left), 0,
        round(rgb.width * right), rgb.height,
    )
    original = rgb.crop(box)
    red, green, blue = original.split()
    processed = ImageOps.autocontrast(
        ImageChops.lighter(red, ImageChops.lighter(green, blue)), cutoff=1
    )
    original_path = destination / "month-digit-original.jpg"
    processed_path = destination / "month-digit-max-channel.png"
    original.resize(
        (max(180, original.width * 3), max(96, original.height * 3)),
        Image.Resampling.LANCZOS,
    ).save(original_path, quality=95)
    processed.resize(
        (max(180, processed.width * 3), max(96, processed.height * 3)),
        Image.Resampling.LANCZOS,
    ).save(processed_path)
    return original_path, processed_path


def _pure_months(path: Path) -> tuple[dict[str, list[str]], dict[str, set[int]]]:
    texts: dict[str, list[str]] = {}
    months = {"mobile": set(), "server": set()}
    for model in ("mobile", "server"):
        try:
            rows = recognize_line(path, model_variant=model)
        except Exception:
            rows = []
        texts[model] = [str(row.text) for row in rows]
        for text in texts[model]:
            compact = re.sub(r"\s+", "", text)
            if re.fullmatch(r"\d{1,2}", compact):
                value = int(compact)
                if 1 <= value <= 12:
                    months[model].add(value)
    return texts, months


def build_report(
    database: Path,
    truth_path: Path,
    artifact_root: Path,
    generated_root: Path,
    backend: str,
) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    records = []
    counts: Counter[str] = Counter()
    for filename, expected in truth.items():
        result = latest.get(filename)
        if result is None:
            counts["missing_result"] += 1
            continue
        if (result.get("date_check") or {}).get("reliable") is True:
            counts["already_reliable"] += 1
            continue
        counts["unreliable"] += 1
        artifact = _tight_artifact(result)
        if artifact is None:
            counts["no_tight_artifact"] += 1
            continue
        source = _resolve_artifact(
            str(artifact.get("date_line_original_url", "")), artifact_root
        )
        if source is None:
            counts["missing_artifact_file"] += 1
            continue
        years = _slot_years_by_model(artifact)
        explicit_months, days = _explicit_components(artifact)
        original_path, processed_path = _month_digit_views(
            source, generated_root / Path(filename).stem
        )
        month_texts, month_values = _pure_months(processed_path)
        common_years = years["mobile"] & years["server"]
        common_months = month_values["mobile"] & month_values["server"]
        common_days = days["mobile"] & days["server"]
        candidate = None
        if (
            len(common_years) == len(common_months) == len(common_days) == 1
            and all(years[model] == common_years for model in years)
            and all(month_values[model] == common_months for model in month_values)
            and all(days[model] == common_days for model in days)
        ):
            year = next(iter(common_years))
            month = next(iter(common_months))
            day = next(iter(common_days))
            if all(
                not values or values == {month}
                for values in explicit_months.values()
            ):
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    candidate = None
        literal_dates = _literal_dates(result)
        if candidate is not None and literal_dates - {candidate}:
            candidate = None
        candidate_text = candidate.isoformat() if candidate else ""
        truth_text = str(expected.get("actual_date", ""))
        required_text = str((expected.get("fields") or {}).get("要求到货", ""))
        selected = bool(candidate_text)
        correct = selected and candidate_text == truth_text
        false_positive = selected and not correct
        counts["selected"] += int(selected)
        counts["correct"] += int(correct)
        counts["false_positive"] += int(false_positive)
        records.append({
            "filename": filename,
            "required": required_text,
            "truth": truth_text,
            "candidate": candidate_text,
            "correct": correct,
            "false_positive": false_positive,
            "year_values": {key: sorted(value) for key, value in years.items()},
            "month_texts": month_texts,
            "month_values": {
                key: sorted(value) for key, value in month_values.items()
            },
            "explicit_month_values": {
                key: sorted(value) for key, value in explicit_months.items()
            },
            "day_values": {key: sorted(value) for key, value in days.items()},
            "literal_dates": sorted(value.isoformat() for value in literal_dates),
            "original_image": str(original_path.resolve()),
            "processed_image": str(processed_path.resolve()),
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "month_digit_window": MONTH_DIGIT_WINDOW,
        "summary": dict(counts),
        "selected": [record for record in records if record["candidate"]],
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="核验固定模板的年份、月份窄槽和显式日跨模型组件共识"
    )
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        args.database,
        args.truth,
        args.artifact_root,
        args.generated_root,
        args.backend,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
