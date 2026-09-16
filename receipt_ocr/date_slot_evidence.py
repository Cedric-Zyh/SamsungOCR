"""Require consistent year, month and day evidence before accepting slots."""

from __future__ import annotations
import re
from datetime import date
from .parser import parse_date, parse_receipt_date
from .date_fragments import (
    _is_date_audit_only_preprocessing,
    _parse_compact_full_date_audit_candidate,
    _parse_date_slot_digit,
    _parse_date_slot_month_day,
    _parse_date_slot_year,
    _parse_full_year_missing_month_day,
    _parse_full_year_month_day_audit,
)
from .date_strict_evidence import (
    _server_cross_geometry_strict_date_from_artifacts,
)


def _adaptive_day_slot_confirms_value(variants: list[dict], expected: int) -> bool:
    """Require raw/color-clean agreement from Mobile and Server."""
    cells: dict[tuple[str, str], set[int]] = {}
    for variant in variants:
        if variant.get("slot") != "自适应日数字槽":
            continue
        model = str(variant.get("model") or "")
        preprocessing = str(variant.get("preprocessing") or "")
        if model not in {"mobile", "server"}:
            continue
        cells[(model, preprocessing)] = {
            int(value)
            for value in variant.get("parsed_components", []) or []
            if str(value).isdigit()
        }
    required_cells = {
        (model, preprocessing)
        for model in ("mobile", "server")
        for preprocessing in ("原始裁剪", "最大通道去彩色")
    }
    return set(cells) == required_cells and all(
        cells[cell] == {expected} for cell in required_cells
    )


def _day_slot_confirms_value(variants: list[dict], expected: int) -> bool:
    """Require exactly one identical day in all four model/transform cells."""
    cells: dict[tuple[str, str], set[int]] = {}
    for variant in variants:
        model = str(variant.get("model") or "")
        preprocessing = str(variant.get("preprocessing") or "")
        if model not in {"mobile", "server"}:
            continue
        values = {
            int(value)
            for value in variant.get("parsed_components", []) or []
            if str(value).isdigit()
        }
        cells[(model, preprocessing)] = values
    required_cells = {
        (model, preprocessing)
        for model in ("mobile", "server")
        for preprocessing in ("最大通道去彩色", "最大通道去彩色并去横线")
    }
    return set(cells) == required_cells and all(
        cells[cell] == {expected} for cell in required_cells
    )


def _white_day_conflict_prefilter_from_artifacts(
    artifacts: list[dict],
    required_text: str,
) -> dict | None:
    """Find a Mobile/Server one-day whole-line disagreement for audit.

    Mobile and Server must each repeat one literal four-digit year/month/day
    value in both tight and wide maximum-channel rows.  The Server value must
    equal the printed requirement, while Mobile must independently read the
    immediately preceding day.  This prefilter only decides whether fixed
    component crops are worth running; it never changes the verdict itself.
    """
    required = parse_date(required_text)
    if required is None:
        return None
    primary = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if primary is None or (
        "vision" not in str(primary.get("ocr_backend", "")).lower()
        or "paddle" not in str(primary.get("secondary_ocr_backend", "")).lower()
    ):
        return None
    preprocessings = {
        "mobile": "日期行最大通道去彩色三倍放大 Mobile 跨几何复核",
        "server": "日期行最大通道去彩色三倍放大 Server 跨几何复核",
    }
    cells: dict[tuple[str, str], set[date]] = {}
    support: dict[str, dict[str, list[str]]] = {
        "mobile": {},
        "server": {},
    }
    for artifact in artifacts:
        geometry = str(artifact.get("variant", ""))
        if geometry not in {"紧凑区域", "宽区域"}:
            continue
        for model, preprocessing in preprocessings.items():
            texts = [
                str(text)
                for variant in artifact.get("date_line_ocr_variants") or []
                if str(variant.get("preprocessing", "")) == preprocessing
                for text in variant.get("ocr_texts") or []
            ]
            values = {
                parsed
                for text in texts
                if (parsed := _parse_full_year_month_day_audit(text)) is not None
            }
            cells[(model, geometry)] = values
            support[model][geometry] = texts
    required_cells = {
        (model, geometry)
        for model in ("mobile", "server")
        for geometry in ("紧凑区域", "宽区域")
    }
    if set(cells) != required_cells or any(
        len(cells[cell]) != 1 for cell in required_cells
    ):
        return None
    mobile_values = cells[("mobile", "紧凑区域")]
    server_values = cells[("server", "紧凑区域")]
    if (
        cells[("mobile", "宽区域")] != mobile_values
        or cells[("server", "宽区域")] != server_values
    ):
        return None
    candidate = next(iter(mobile_values))
    conflict = next(iter(server_values))
    if not (
        conflict == required
        and candidate.year == conflict.year
        and candidate.month == conflict.month
        and candidate.day >= 10
        and conflict.day >= 10
        and (conflict - candidate).days == 1
    ):
        return None
    return {
        "date": candidate,
        "conflict": conflict,
        "support": support,
    }


def _white_day_conflict_audit_candidate(
    prefilter: dict | None,
    component_variants: list[dict],
) -> date | None:
    """Confirm the Mobile candidate as a low-confidence review suggestion.

    Both Paddle model sizes must read the candidate's complete year from the
    maximum-channel year context and its two-digit day from *both* white-canvas
    day-context variants.  The result remains an audit suggestion because all
    component images originate from one physical row and whole-line Server
    still disagrees.
    """
    if not prefilter:
        return None
    candidate = prefilter.get("date")
    if not isinstance(candidate, date):
        return None
    years: dict[str, set[int]] = {"mobile": set(), "server": set()}
    day_cells: dict[tuple[str, str], set[int]] = {}
    for variant in component_variants:
        model = str(variant.get("model", ""))
        if model not in years:
            continue
        slot = str(variant.get("slot", ""))
        preprocessing = str(variant.get("preprocessing", ""))
        texts = [str(text) for text in variant.get("ocr_texts") or []]
        if slot == "整行冲突完整年份槽位":
            years[model].update(
                parsed
                for text in texts
                if (parsed := _parse_date_slot_year(text)) is not None
            )
        elif slot == "整行冲突白边日上下文槽位":
            values: set[int] = set()
            for text in texts:
                compact = re.sub(r"\s+", "", text)
                match = re.search(r"月(\d{2})(?:日|月)(?!\d)", compact)
                if match and 10 <= int(match.group(1)) <= 31:
                    values.add(int(match.group(1)))
            day_cells[(model, preprocessing)] = values
    if any(years[model] != {candidate.year} for model in years):
        return None
    required_day_cells = {
        (model, preprocessing)
        for model in ("mobile", "server")
        for preprocessing in ("白边标准化", "裁后白边标准化")
    }
    if set(day_cells) != required_day_cells or any(
        day_cells[cell] != {candidate.day} for cell in required_day_cells
    ):
        return None
    return candidate


def _cross_model_server_strict_component_date(
    candidate: date | None,
    component_variants: list[dict],
    existing_texts: list[str],
) -> date | None:
    """Confirm a repeated Server date with fixed Mobile/Server components.

    Both models must independently expose the candidate year and explicit
    month/day on one non-destructive maximum-channel image.  Partial whole-line
    conflicts may vary only in month while retaining the OCR-owned year/day;
    this handles a stamped handwritten ``9`` that resembles ``3`` without
    repairing any component from the required date.
    """
    if candidate is None:
        return None
    years = {"mobile": set(), "server": set()}
    month_days = {"mobile": set(), "server": set()}
    for variant in component_variants:
        model = str(variant.get("model", ""))
        if model not in years:
            continue
        slot = str(variant.get("slot", ""))
        for text in variant.get("ocr_texts") or []:
            if slot == "Server双几何完整年份上下文槽位":
                parsed_year = _parse_date_slot_year(str(text))
                if parsed_year is not None:
                    years[model].add(parsed_year)
            elif slot == "Server双几何月日上下文槽位":
                parsed_month_day = _parse_date_slot_month_day(str(text))
                if parsed_month_day is not None:
                    month_days[model].add(parsed_month_day)
    expected_year = {candidate.year}
    expected_month_day = {(candidate.month, candidate.day)}
    if not all(
        years[model] == expected_year and month_days[model] == expected_month_day
        for model in ("mobile", "server")
    ):
        return None

    strict_dates = {
        parsed
        for text in existing_texts
        if (parsed := parse_date(str(text))) is not None
    }
    if strict_dates - {candidate}:
        return None
    receipt_dates = {
        parsed
        for text in existing_texts
        if (parsed := parse_receipt_date(str(text), candidate)) is not None
    }
    conflicts = receipt_dates - {candidate}
    if len(conflicts) > 2 or any(
        value.year != candidate.year
        or value.day != candidate.day
        or value.month == candidate.month
        for value in conflicts
    ):
        return None
    return candidate


def _server_strict_component_consensus_from_artifacts(
    artifacts: list[dict],
) -> dict | None:
    """Rebuild the Server-double-geometry component decision from artifacts."""
    candidate = _server_cross_geometry_strict_date_from_artifacts(artifacts)
    if candidate is None:
        return None
    tight = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight is None or (
        "vision" not in str(tight.get("ocr_backend", "")).lower()
        or "paddle" not in str(tight.get("secondary_ocr_backend", "")).lower()
    ):
        return None
    component_variants = [
        variant
        for variant in tight.get("date_slot_ocr_variants") or []
        if str(variant.get("slot", ""))
        in {
            "Server双几何完整年份上下文槽位",
            "Server双几何月日上下文槽位",
            "Server双几何日上下文审计槽位",
        }
    ]
    existing_texts = [
        str(text)
        for artifact in artifacts
        for key in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        )
        for variant in artifact.get(key) or []
        if not _is_date_audit_only_preprocessing(str(variant.get("preprocessing", "")))
        for text in variant.get("ocr_texts") or []
    ]
    confirmed = _cross_model_server_strict_component_date(
        candidate, component_variants, existing_texts
    )
    if confirmed is None:
        return None
    return {
        "date": confirmed,
        "support": {
            "models": ["mobile", "server"],
            "server_geometries": ["紧凑区域", "宽区域"],
            "slot_preprocessing": "最大通道去彩色",
            "year": confirmed.year,
            "month": confirmed.month,
            "day": confirmed.day,
        },
    }


def _required_month_slot_conflict_prefilter_from_artifacts(
    artifacts: list[dict], required_text: str
) -> dict | None:
    """Find the unique strict table-line month-conflict shape.

    The required date must already be a literal complete OCR result from both
    Paddle model sizes on opposite tight/wide geometries.  Exactly one other
    literal complete date may exist, and it must come only from a Server
    table-line-removal view while preserving the required year and day. This
    first stage deliberately ignores component slots so the live pipeline
    knows when it is worth generating them.  It never changes a verdict.
    """
    required = parse_date(required_text)
    if required is None:
        return None
    primary_variants = {"紧凑区域", "宽区域"}
    tight = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight is None or (
        "vision" not in str(tight.get("ocr_backend", "")).lower()
        or "paddle" not in str(tight.get("secondary_ocr_backend", "")).lower()
    ):
        # A Windows/Paddle-only run must keep the documented human-review
        # policy even if it happens to execute both Paddle model sizes.
        return None

    observations: list[dict] = []
    for artifact in artifacts:
        geometry = str(artifact.get("variant", ""))
        if geometry not in primary_variants:
            continue
        groups = (
            ("ocr_variants", str(artifact.get("ocr_backend", ""))),
            (
                "secondary_ocr_variants",
                str(artifact.get("secondary_ocr_backend", "")),
            ),
            (
                "date_line_ocr_variants",
                str(artifact.get("date_line_ocr_backend", "")),
            ),
        )
        for key, default_backend in groups:
            for evidence in artifact.get(key) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                label = f"{default_backend} {preprocessing}".lower()
                if "server" in label or "大模型" in label:
                    engine = "server"
                elif "mobile" in label or "paddleocr" in label:
                    engine = "mobile"
                elif "vision" in label:
                    engine = "vision"
                else:
                    continue
                for raw_text in evidence.get("ocr_texts") or []:
                    text = str(raw_text).strip()
                    strict = parse_date(text)
                    parsed = strict or parse_receipt_date(text, required)
                    if parsed is None:
                        continue
                    observations.append(
                        {
                            "date": parsed,
                            "strict": strict is not None,
                            "engine": engine,
                            "geometry": geometry,
                            "preprocessing": preprocessing,
                        }
                    )

    required_cells = {
        (item["engine"], item["geometry"])
        for item in observations
        if item["strict"]
        and item["date"] == required
        and item["engine"] in {"mobile", "server"}
    }
    if not any(
        left_engine != right_engine and left_geometry != right_geometry
        for left_engine, left_geometry in required_cells
        for right_engine, right_geometry in required_cells
    ):
        return None

    strict_conflicts = [
        item for item in observations if item["strict"] and item["date"] != required
    ]
    conflict_dates = {item["date"] for item in strict_conflicts}
    if len(conflict_dates) != 1:
        return None
    conflict = next(iter(conflict_dates))
    if (
        conflict.year != required.year
        or conflict.day != required.day
        or conflict.month == required.month
        or any(item["engine"] != "server" for item in strict_conflicts)
        or any("去表格线" not in item["preprocessing"] for item in strict_conflicts)
    ):
        return None
    if {item["date"] for item in observations} - {required, conflict}:
        return None

    return {
        "date": required,
        "conflict": conflict,
        "required_cells": [
            {"engine": engine, "geometry": geometry}
            for engine, geometry in sorted(required_cells)
        ],
    }


def _required_month_slot_conflict_consensus_from_artifacts(
    artifacts: list[dict], required_text: str
) -> dict | None:
    """Resolve a prefiltered month conflict with four slot OCR cells."""
    prefilter = _required_month_slot_conflict_prefilter_from_artifacts(
        artifacts, required_text
    )
    if prefilter is None:
        return None
    required = prefilter["date"]
    tight = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight is None:
        return None
    month_cells: dict[tuple[str, str], set[int]] = {}
    for variant in tight.get("date_slot_ocr_variants") or []:
        if str(variant.get("slot", "")) != "月份数字窄槽":
            continue
        model = str(variant.get("model", ""))
        preprocessing = str(variant.get("preprocessing", ""))
        if model not in {"mobile", "server"}:
            continue
        values = {
            parsed
            for text in variant.get("ocr_texts") or []
            if (parsed := _parse_date_slot_digit(str(text), maximum=12)) is not None
        }
        month_cells[(model, preprocessing)] = values
    expected_cells = {
        (model, preprocessing)
        for model in ("mobile", "server")
        for preprocessing in ("最大通道去彩色", "最大通道去彩色并去横线")
    }
    if set(month_cells) != expected_cells or any(
        month_cells[cell] != {required.month} for cell in expected_cells
    ):
        return None
    return {**prefilter, "month": required.month}


def _same_geometry_missing_month_consensus_from_artifacts(
    artifacts: list[dict],
    required_text: str,
    slot_variants: list[dict] | None = None,
) -> dict | None:
    """Confirm a required date whose Mobile row omits only the month digit.

    Server supplies a literal full date. Mobile must independently preserve
    the same four-digit year and two-digit day on the same maximum-channel
    crop while showing an empty month position (for example
    ``2025年月11日``). The missing month then comes from the fixed month-digit
    crop: both models must read it, at least three of the four
    model/preprocessing cells must agree, and no cell may contain another
    value. Any remaining whole-line date may only be the same two-digit day
    truncated to one of its printed digits.
    """
    required = parse_date(required_text)
    if required is None or required.day < 10:
        return None
    primary = [
        item for item in artifacts if item.get("variant") in {"紧凑区域", "宽区域"}
    ]
    tight = next(
        (item for item in primary if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight is None or (
        "vision" not in str(tight.get("ocr_backend", "")).lower()
        or "paddle" not in str(tight.get("secondary_ocr_backend", "")).lower()
    ):
        return None

    server_dates: dict[str, set[date]] = {}
    mobile_year_days: dict[str, set[tuple[int, int]]] = {}
    all_texts: list[str] = []
    strict_dates: set[date] = set()
    expected_prefix = "日期行最大通道去彩色三倍放大 "
    for artifact in primary:
        geometry = str(artifact.get("variant", ""))
        for key in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for variant in artifact.get(key) or []:
                preprocessing = str(variant.get("preprocessing", ""))
                texts = [str(value) for value in variant.get("ocr_texts") or []]
                if not _is_date_audit_only_preprocessing(preprocessing):
                    all_texts.extend(texts)
                    strict_dates.update(
                        parsed
                        for text in texts
                        if (parsed := parse_date(text)) is not None
                    )
                if key != "date_line_ocr_variants" or not preprocessing.startswith(
                    expected_prefix
                ):
                    continue
                if "Server 跨几何复核" in preprocessing:
                    server_dates.setdefault(geometry, set()).update(
                        parsed
                        for text in texts
                        if (parsed := parse_date(text)) is not None
                    )
                elif "Mobile 跨几何复核" in preprocessing:
                    mobile_year_days.setdefault(geometry, set()).update(
                        parsed
                        for text in texts
                        if (parsed := _parse_full_year_missing_month_day(text))
                        is not None
                    )

    candidates = []
    for geometry in {"紧凑区域", "宽区域"}:
        if server_dates.get(geometry) != {required}:
            continue
        if mobile_year_days.get(geometry) != {(required.year, required.day)}:
            continue
        candidates.append(geometry)
    if len(candidates) != 1 or strict_dates != {required}:
        return None

    variants = slot_variants
    if variants is None:
        variants = list(tight.get("date_slot_ocr_variants") or [])
    month_cells: dict[tuple[str, str], set[int]] = {}
    for variant in variants:
        if str(variant.get("slot", "")) != "月份数字窄槽":
            continue
        model = str(variant.get("model", ""))
        preprocessing = str(variant.get("preprocessing", ""))
        if model not in {"mobile", "server"} or preprocessing not in {
            "最大通道去彩色",
            "最大通道去彩色并去横线",
        }:
            continue
        month_cells[(model, preprocessing)] = {
            parsed
            for text in variant.get("ocr_texts") or []
            if (parsed := _parse_date_slot_digit(str(text), maximum=12)) is not None
        }
    expected_cells = {
        (model, preprocessing)
        for model in ("mobile", "server")
        for preprocessing in ("最大通道去彩色", "最大通道去彩色并去横线")
    }
    if set(month_cells) != expected_cells:
        return None
    if any(values - {required.month} for values in month_cells.values()):
        return None
    supporting_cells = [
        cell for cell, values in month_cells.items() if values == {required.month}
    ]
    if (
        len(supporting_cells) < 3
        or {cell[0] for cell in supporting_cells} != {"mobile", "server"}
        or not all(
            month_cells[(model, "最大通道去彩色")] == {required.month}
            for model in ("mobile", "server")
        )
    ):
        return None

    parsed_dates = {
        parsed
        for text in all_texts
        if (parsed := parse_receipt_date(text, required)) is not None
    }
    allowed_days = {required.day, required.day // 10, required.day % 10}
    if any(
        value.year != required.year
        or value.month != required.month
        or value.day not in allowed_days
        for value in parsed_dates
    ):
        return None
    conflicts = sorted(parsed_dates - {required})
    return {
        "date": required,
        "geometry": candidates[0],
        "supporting_month_cells": [
            {"model": model, "preprocessing": preprocessing}
            for model, preprocessing in sorted(supporting_cells)
        ],
        "conflicts": conflicts,
    }


def _date_component_consensus_from_artifacts(
    artifacts: list[dict],
) -> dict | None:
    """Build a date only from independent fixed-template OCR components.

    This route repairs the special case where a thin handwritten month digit
    touches the form/stamp and whole-line OCR drops it. Mobile and Server must
    agree separately on a complete year, the digit-only month crop, and an
    explicit day ending in ``日``. The printed required date is intentionally
    not an input. Any literal full-date conflict vetoes the candidate.
    """
    tight = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight is None:
        return None
    # This is a macOS Hybrid corroboration route. A pure Paddle/Windows run
    # may save the same diagnostic slots, but must preserve the documented
    # single-backend safety policy and remain pending review.
    if (
        "vision" not in str(tight.get("ocr_backend", "")).lower()
        or "paddle" not in str(tight.get("secondary_ocr_backend", "")).lower()
    ):
        return None

    years = {"mobile": set(), "server": set()}
    months = {"mobile": set(), "server": set()}
    for variant in tight.get("date_slot_ocr_variants") or []:
        model = str(variant.get("model", ""))
        if model not in years or variant.get("preprocessing") != "最大通道去彩色":
            continue
        slot = str(variant.get("slot", ""))
        for text in variant.get("ocr_texts") or []:
            if slot == "完整年份槽位":
                parsed_year = _parse_date_slot_year(str(text))
                if parsed_year is not None:
                    years[model].add(parsed_year)
            elif slot == "月份数字窄槽":
                parsed_month = _parse_date_slot_digit(str(text), maximum=12)
                if parsed_month is not None:
                    months[model].add(parsed_month)

    explicit_months = {"mobile": set(), "server": set()}
    days = {"mobile": set(), "server": set()}
    default_backend = str(tight.get("date_line_ocr_backend", ""))
    for variant in tight.get("date_line_ocr_variants") or []:
        preprocessing = str(variant.get("preprocessing", ""))
        backend = (
            "PaddleOCR Server"
            if "Server" in preprocessing or "大模型" in preprocessing
            else default_backend
        )
        lowered = backend.lower()
        model = (
            "server"
            if "server" in lowered or "大模型" in backend
            else "mobile" if "mobile" in lowered or "paddleocr" in lowered else ""
        )
        if model not in days:
            continue
        for text in variant.get("ocr_texts") or []:
            compact = re.sub(r"\s+", "", str(text))
            for value in re.findall(r"(?<!\d)(\d{1,2})月", compact):
                month = int(value)
                if 1 <= month <= 12:
                    explicit_months[model].add(month)
            for value in re.findall(r"(?<!\d)(\d{1,2})日", compact):
                day = int(value)
                if 1 <= day <= 31:
                    days[model].add(day)

    common_years = years["mobile"] & years["server"]
    common_months = months["mobile"] & months["server"]
    common_days = days["mobile"] & days["server"]
    if not (
        len(common_years) == len(common_months) == len(common_days) == 1
        and all(years[model] == common_years for model in years)
        and all(months[model] == common_months for model in months)
        and all(days[model] == common_days for model in days)
    ):
        return None
    month = next(iter(common_months))
    if any(values and values != {month} for values in explicit_months.values()):
        return None
    try:
        candidate = date(next(iter(common_years)), month, next(iter(common_days)))
    except ValueError:
        return None

    literal_dates: set[date] = set()
    for artifact in artifacts:
        for key in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for variant in artifact.get(key) or []:
                for text in variant.get("ocr_texts") or []:
                    parsed = parse_date(str(text)) or (
                        _parse_compact_full_date_audit_candidate(str(text))
                    )
                    if parsed is not None:
                        literal_dates.add(parsed)
    if literal_dates - {candidate}:
        return None
    return {
        "date": candidate,
        "support": {
            "year": candidate.year,
            "month": candidate.month,
            "day": candidate.day,
            "models": ["mobile", "server"],
            "month_digit_preprocessing": "最大通道去彩色",
        },
    }


def _cross_model_slot_required_date(
    year_variants: list[dict],
    month_day_variants: list[dict],
    required_text: str,
    existing_texts: list[str],
) -> date | None:
    """Promote only a fully OCR-owned slot date matching the requirement.

    Mobile and Server must each expose exactly the same complete four-digit
    year and explicit month/day across the approved color-suppressed views.
    The printed required date is only the final comparison target; it never
    fills a missing component. Any other parseable date on the physical line
    vetoes promotion, so recognized mismatches remain in manual review.
    """
    required = parse_date(required_text)
    if required is None:
        return None

    years = {"mobile": set(), "server": set()}
    month_days = {"mobile": set(), "server": set()}
    for variant in year_variants:
        model = str(variant.get("model", ""))
        if model not in years:
            continue
        for text in variant.get("ocr_texts", []) or []:
            parsed = _parse_date_slot_year(str(text))
            if parsed is not None:
                years[model].add(parsed)
    for variant in month_day_variants:
        model = str(variant.get("model", ""))
        if model not in month_days:
            continue
        for text in variant.get("ocr_texts", []) or []:
            parsed = _parse_date_slot_month_day(str(text))
            if parsed is not None:
                month_days[model].add(parsed)

    expected_year = {required.year}
    expected_month_day = {(required.month, required.day)}
    if not all(
        years[model] == expected_year and month_days[model] == expected_month_day
        for model in ("mobile", "server")
    ):
        return None

    existing_dates: set[date] = set()
    for text in existing_texts:
        parsed = parse_date(str(text)) or parse_receipt_date(str(text), required)
        if parsed is not None:
            existing_dates.add(parsed)
    if existing_dates - {required}:
        return None
    return required
