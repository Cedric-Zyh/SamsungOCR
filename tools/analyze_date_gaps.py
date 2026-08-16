from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.parser import parse_date, parse_receipt_date
from receipt_ocr.analyzer import _parse_compact_full_date_audit_candidate


EVIDENCE_COMPLETENESS_RANK = {
    "unknown": 0,
    "month_day_only": 1,
    "malformed_compact_year": 2,
    "partial_year": 2,
    "complete_four_digit_year": 3,
}


def _engine(label: str) -> str:
    lowered = label.lower()
    if "server" in lowered or "大模型" in label:
        return "server"
    if "mobile" in lowered or "paddleocr" in lowered:
        return "mobile"
    if "vision" in lowered:
        return "vision"
    return "unknown"


def _observations(result: dict) -> list[dict]:
    output: list[dict] = []
    for artifact in (result.get("processing_artifacts") or {}).get("date") or []:
        variant = str(artifact.get("variant", ""))
        groups = (
            ("ocr_variants", str(artifact.get("ocr_backend", ""))),
            ("secondary_ocr_variants", str(artifact.get("secondary_ocr_backend", ""))),
            ("date_line_ocr_variants", str(artifact.get("date_line_ocr_backend", ""))),
        )
        for key, default_backend in groups:
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                backend = default_backend
                if "Vision" in preprocessing:
                    backend = "macOS Vision"
                elif "Server" in preprocessing or "大模型" in preprocessing:
                    backend = "PaddleOCR Server"
                for text in evidence.get("ocr_texts") or []:
                    value = str(text).strip()
                    if not value:
                        continue
                    output.append({
                        "text": value,
                        "engine": _engine(backend),
                        "backend": backend,
                        "variant": variant,
                        "preprocessing": preprocessing,
                    })
        slot_candidate = str(artifact.get("date_slot_candidate", "") or "")
        if slot_candidate:
            source = str(artifact.get("date_slot_candidate_source", "") or "")
            if "Vision 单路径" in source:
                output.append({
                    "text": slot_candidate,
                    "engine": "hybrid_slot",
                    "backend": "Paddle 双模型年份 + macOS Vision 月日",
                    "variant": variant,
                    "preprocessing": (
                        "固定模板白底年份 + Vision 月日槽位（低置信度人工候选）"
                    ),
                })
            else:
                # The established production slot candidate exists only after
                # Mobile and Server independently agree on both OCR-owned
                # components. Emit one observation per contributing engine.
                for backend in (
                    "PaddleOCR PP-OCRv5 Mobile", "PaddleOCR Server"
                ):
                    output.append({
                        "text": slot_candidate,
                        "engine": _engine(backend),
                        "backend": backend,
                        "variant": variant,
                        "preprocessing": (
                            "固定模板年份/月日槽位组合（人工候选）"
                        ),
                    })
    return output


def _evidence_completeness(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    if re.search(r"(?<!\d)20\d{2}(?:年)?\d{4}日(?!\d)", compact):
        return "complete_four_digit_year"
    if re.search(r"(?<!\d)20\d{2}[年./-]\d{1,2}(?:月|[./-])", compact):
        return "complete_four_digit_year"
    if re.search(r"\d{1,3}年\d{1,2}月\d{1,2}", compact):
        return "partial_year"
    if re.search(r"\d{3,4}月\d{1,2}日?", compact):
        return "malformed_compact_year"
    if re.search(r"\d{1,2}月\d{1,2}日?", compact):
        return "month_day_only"
    return "unknown"


def _parse_evidence_date(text: str, required):
    """Keep literal strict dates before applying required-date repair.

    The production parser intentionally rejects a strict four-digit year that
    differs from the printed required year.  A gap report must still retain
    that literal OCR evidence because a genuinely anomalous handwritten year
    belongs in the human-review audit.
    """
    return (
        parse_date(text)
        or _parse_compact_full_date_audit_candidate(text)
        or parse_receipt_date(text, required)
    )


def _date_artifact_metadata(result: dict) -> dict:
    artifacts = (result.get("processing_artifacts") or {}).get("date") or []
    urls: list[dict] = []
    has_table_clean = False
    for artifact in artifacts:
        item = {
            "variant": str(artifact.get("variant", "")),
            "original_url": str(artifact.get("date_line_original_url", "") or ""),
            "color_clean_url": str(
                artifact.get("date_line_color_clean_url", "") or ""
            ),
            "table_clean_url": str(
                artifact.get("date_line_table_clean_url", "") or ""
            ),
            "slot_year_url": str(
                artifact.get("date_slot_year_processed_url", "") or ""
            ),
            "slot_month_day_url": str(
                artifact.get("date_slot_month_day_processed_url", "") or ""
            ),
            "slot_year_white_url": str(
                artifact.get("date_slot_year_white_url", "") or ""
            ),
            "slot_month_day_vision_url": str(
                artifact.get("date_slot_month_day_vision_url", "") or ""
            ),
            "far_lower_padded_line_url": str(
                artifact.get("far_lower_padded_line_url", "") or ""
            ),
        }
        if item["table_clean_url"]:
            has_table_clean = True
        urls.append(item)
    return {
        "date_artifact_variants": len(artifacts),
        "has_table_clean_artifact": has_table_clean,
        "date_artifact_urls": urls,
    }


def _gap_category(
    truth_evidence: list[dict],
    other_evidence: list[dict],
    truth_engines: list[str],
    has_table_clean: bool,
) -> str:
    if truth_evidence and other_evidence:
        return "真值与其他日期冲突"
    if truth_evidence:
        strongest = max(
            (
                EVIDENCE_COMPLETENESS_RANK.get(
                    str(item.get("completeness", "unknown")), 0
                )
                for item in truth_evidence
            ),
            default=0,
        )
        engines = set(truth_engines) - {"unknown"}
        if strongest >= EVIDENCE_COMPLETENESS_RANK["complete_four_digit_year"]:
            return "无冲突完整年份候选"
        if len(engines) >= 2:
            return "无冲突跨引擎残缺年份候选"
        return "无冲突单引擎残缺候选"
    if other_evidence:
        return "仅有其他日期候选"
    if has_table_clean:
        return "当前预处理仍无可解析候选"
    return "旧版中间图无可解析候选"


def _strict_engines(evidence: list[dict]) -> list[str]:
    return sorted({
        str(item.get("engine", "unknown"))
        for item in evidence
        if item.get("completeness") == "complete_four_digit_year"
        and item.get("engine") in {"mobile", "server", "vision"}
    })


def _conflict_profile(
    required_text: str,
    truth_text: str,
    truth_evidence: list[dict],
    other_evidence: list[dict],
) -> str:
    """Describe why correct and incorrect date evidence coexist."""
    if not truth_evidence or not other_evidence:
        return ""
    required = parse_date(required_text)
    truth = parse_date(truth_text)
    other_dates = {
        parse_date(str(item.get("parsed", ""))) for item in other_evidence
    } - {None}
    truth_strict = _strict_engines(truth_evidence)
    other_strict = _strict_engines(other_evidence)
    if (
        required is not None
        and truth is not None
        and truth.year != required.year
        and required in other_dates
        and truth_strict
    ):
        return "跨年份严格真值与要求年份修复冲突"
    if truth_strict and other_strict:
        return "完整日期相互冲突"
    if len(truth_strict) >= 2 and not other_strict:
        return "跨引擎严格真值与残缺其他候选冲突"
    truth_engines = {
        str(item.get("engine", "unknown")) for item in truth_evidence
    } - {"unknown"}
    other_engines = {
        str(item.get("engine", "unknown")) for item in other_evidence
    } - {"unknown"}
    if len(truth_engines) >= 2 and len(other_engines) == 1:
        return "跨引擎真值与单引擎干扰冲突"
    if len(truth_engines) >= 2:
        return "多引擎多日期冲突"
    return "单引擎或残缺证据冲突"


def _latest_original_results(database: Path, backend: str) -> dict[str, dict]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT id,filename,original_result_json FROM results ORDER BY id"
    ).fetchall()
    connection.close()
    latest: dict[str, dict] = {}
    for result_id, filename, payload in rows:
        try:
            result = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("ocr_backend") != backend:
            continue
        result["id"] = result_id
        result["filename"] = filename
        latest[filename] = result
    return latest


def build_report(database: Path, truth_path: Path, backend: str) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    rows = []
    summary = Counter()
    engine_truth = Counter()
    pattern_counts: dict[str, Counter] = defaultdict(Counter)
    gap_categories: Counter[str] = Counter()
    conflict_profiles: Counter[str] = Counter()
    for filename, expected in truth.items():
        result = latest.get(filename)
        if not result:
            summary["missing_result"] += 1
            continue
        date_check = result.get("date_check") or {}
        if date_check.get("reliable") is True:
            summary["already_reliable"] += 1
            continue
        summary["unreliable"] += 1
        required = parse_date(str((expected.get("fields") or {}).get("要求到货", "")))
        truth_date = str(expected.get("actual_date", ""))
        candidates: dict[str, list[dict]] = defaultdict(list)
        for observation in _observations(result):
            parsed = _parse_evidence_date(observation["text"], required)
            if parsed is None:
                continue
            parsed_text = parsed.isoformat()
            item = dict(
                observation,
                parsed=parsed_text,
                completeness=_evidence_completeness(observation["text"]),
            )
            candidates[parsed_text].append(item)
            pattern_counts[observation["engine"]][observation["preprocessing"]] += 1

        truth_evidence = candidates.get(truth_date, [])
        other_evidence = [
            item for value, items in candidates.items() if value != truth_date for item in items
        ]
        truth_engines = sorted({item["engine"] for item in truth_evidence})
        other_dates = sorted({item["parsed"] for item in other_evidence})
        if truth_evidence:
            summary["truth_candidate_present"] += 1
            for name in truth_engines:
                engine_truth[name] += 1
        if len(set(truth_engines) - {"unknown"}) >= 2:
            summary["truth_across_two_engines"] += 1
        if other_evidence:
            summary["other_candidate_present"] += 1
        if truth_evidence and other_evidence:
            summary["truth_and_other_conflict"] += 1
        if not candidates:
            summary["no_parseable_candidate"] += 1
        artifact_metadata = _date_artifact_metadata(result)
        category = _gap_category(
            truth_evidence,
            other_evidence,
            truth_engines,
            bool(artifact_metadata["has_table_clean_artifact"]),
        )
        gap_categories[category] += 1
        conflict_profile = _conflict_profile(
            required.isoformat() if required else "",
            truth_date,
            truth_evidence,
            other_evidence,
        )
        if conflict_profile:
            conflict_profiles[conflict_profile] += 1
        rows.append({
            "filename": filename,
            "result_id": result.get("id"),
            "required": required.isoformat() if required else "",
            "truth": truth_date,
            "machine_actual": str(date_check.get("actual", "")),
            "machine_status": str(date_check.get("status", "")),
            "machine_confidence": date_check.get("confidence", 0),
            "message": str(date_check.get("message", "")),
            "truth_engines": truth_engines,
            "truth_strict_engines": _strict_engines(truth_evidence),
            "other_strict_engines": _strict_engines(other_evidence),
            "other_dates": other_dates,
            "truth_evidence": truth_evidence,
            "other_evidence": other_evidence,
            "gap_category": category,
            "conflict_profile": conflict_profile,
            **artifact_metadata,
        })
    rows.sort(key=lambda row: (
        not bool(row["truth_evidence"]),
        -len(row["truth_engines"]),
        bool(row["other_evidence"]),
        row["filename"],
    ))
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": dict(summary),
        "truth_candidate_by_engine": dict(engine_truth),
        "parseable_patterns": {
            engine: dict(counter.most_common()) for engine, counter in pattern_counts.items()
        },
        "gap_categories": dict(gap_categories),
        "conflict_profiles": dict(conflict_profiles),
        "samples": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="分析最新机器原始结果中未可靠日期的 OCR 证据缺口"
    )
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.database, args.truth, args.backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
