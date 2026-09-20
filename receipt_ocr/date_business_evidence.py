"""Validate partial date evidence against explicit business context."""

from __future__ import annotations
import re
from datetime import date
from .parser import parse_date, parse_receipt_date
from .ocr_types import TextObservation
from .paddle_ocr import is_lightweight_backend as _is_lightweight_line_backend
from .date_fragments import (
    _parse_compact_full_date_audit_candidate,
    _parse_date_slot_digit,
    _parse_full_year_month_day_audit,
    _parse_partial_year_month_day_audit,
)


def _same_geometry_partial_year_required_consensus_from_artifacts(
    artifacts: list[dict],
    required_text: str,
    creation_text: str,
    tracking_text: str,
) -> dict | None:
    """Recover one missing year suffix from three independent business dates.

    The handwritten row must still own the three-digit ``20x`` prefix and the
    complete month/day.  On the wide geometry, Paddle Mobile must read that
    structure from the table-line-cleaned region while Paddle Server repeats
    it in at least two separately labelled enhanced line passes.  The printed
    required date, creation date, and the date encoded in the tracking number
    must all independently agree on the missing four-digit year.  Any other
    valid literal or repaired date keeps the sample in manual review.
    """
    required = parse_date(required_text)
    creation = parse_date(creation_text)
    tracking_match = re.search(r"W(20\d{2})(\d{2})(\d{2})", tracking_text)
    tracking = parse_date("-".join(tracking_match.groups()) if tracking_match else "")
    if (
        required is None
        or creation is None
        or tracking is None
        or len({required.year, creation.year, tracking.year}) != 1
        or not (creation <= required and tracking <= required)
        or (required - creation).days > 7
        or (required - tracking).days > 7
        or required.day < 10
    ):
        return None
    primary = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    wide = next(
        (item for item in artifacts if item.get("variant") == "宽区域"),
        None,
    )
    if (
        primary is None
        or wide is None
        or (
            "vision" not in str(primary.get("ocr_backend", "")).lower()
            or "paddle" not in str(primary.get("secondary_ocr_backend", "")).lower()
        )
    ):
        return None

    def parse_partial(raw: str) -> date | None:
        compact = re.sub(r"\s+", "", str(raw).replace("O", "0").replace("o", "0"))
        match = re.fullmatch(r"(20\d)年(\d{1,2})月(\d{2})(?:日)?", compact)
        if match is None or match.group(1) != str(required.year)[:3]:
            return None
        try:
            return date(required.year, int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    mobile_support = [
        {
            "preprocessing": str(evidence.get("preprocessing", "")),
            "text": str(text),
        }
        for evidence in wide.get("secondary_ocr_variants") or []
        if str(evidence.get("preprocessing", "")) == "去表格线"
        for text in evidence.get("ocr_texts") or []
        if parse_partial(str(text)) == required
    ]
    server_support = [
        {
            "preprocessing": str(evidence.get("preprocessing", "")),
            "text": str(text),
        }
        for evidence in wide.get("date_line_ocr_variants") or []
        if "Server" in str(evidence.get("preprocessing", ""))
        for text in evidence.get("ocr_texts") or []
        if parse_partial(str(text)) == required
    ]
    if (
        not mobile_support
        or len({item["preprocessing"] for item in server_support}) < 2
    ):
        return None

    observed_dates: set[date] = set()
    for artifact in artifacts:
        if artifact.get("variant") not in {"紧凑区域", "宽区域"}:
            continue
        for group in (
            artifact.get("ocr_variants") or [],
            artifact.get("secondary_ocr_variants") or [],
            artifact.get("date_line_ocr_variants") or [],
        ):
            for evidence in group:
                for raw in evidence.get("ocr_texts") or []:
                    text = str(raw)
                    parsed = (
                        parse_date(text)
                        or _parse_compact_full_date_audit_candidate(text)
                        or parse_partial(text)
                        or parse_receipt_date(text, required)
                    )
                    if parsed is not None:
                        observed_dates.add(parsed)
    if observed_dates != {required}:
        return None
    return {
        "date": required,
        "support": {
            "mobile": mobile_support,
            "server": server_support,
        },
    }


def _unanimous_month_day_business_year_consensus_from_artifacts(
    artifacts: list[dict],
    required_text: str,
    creation_text: str,
    tracking_text: str,
) -> dict | None:
    """Replace one demonstrably stale OCR year while preserving OCR month/day.

    Every explicit handwritten month/day must be identical, and that value
    must be observed across both Paddle models and both tight/wide geometries.
    Exactly one literal full date may exist; its year must be at least one year
    before document creation while keeping the unanimous month/day. The new
    year is admitted only when the printed requirement, creation date, and
    waybill-encoded date independently agree and the resulting receipt date
    falls inside their seven-day business window. This route is macOS Hybrid
    only and never borrows a missing month or day from a printed field.
    """
    required = parse_date(required_text)
    creation = parse_date(creation_text)
    tracking_match = re.search(r"W(20\d{2})(\d{2})(\d{2})", tracking_text)
    tracking = parse_date("-".join(tracking_match.groups()) if tracking_match else "")
    if (
        required is None
        or creation is None
        or tracking is None
        or len({required.year, creation.year, tracking.year}) != 1
    ):
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

    def explicit_month_day(raw: str) -> tuple[int, int] | None:
        compact = re.sub(r"\s+", "", str(raw).replace("O", "0").replace("o", "0"))
        match = re.search(r"\d{2,6}年(\d{1,2})月(\d{1,2})日(?!\d)", compact)
        if match is None:
            return None
        month, day = (int(value) for value in match.groups())
        try:
            date(required.year, month, day)
        except ValueError:
            return None
        return month, day

    observations: list[dict] = []
    all_month_days: set[tuple[int, int]] = set()
    strict_dates: set[date] = set()
    for artifact in artifacts:
        geometry = str(artifact.get("variant", ""))
        if geometry not in {"紧凑区域", "宽区域"}:
            continue
        for group_name in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for evidence in artifact.get(group_name) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                if "Server" in preprocessing:
                    model = "server"
                elif (
                    "Mobile" in preprocessing or group_name == "secondary_ocr_variants"
                ):
                    model = "mobile"
                else:
                    model = ""
                for raw in evidence.get("ocr_texts") or []:
                    text = str(raw)
                    month_day = explicit_month_day(text)
                    if month_day is not None:
                        all_month_days.add(month_day)
                        if model:
                            observations.append(
                                {
                                    "model": model,
                                    "geometry": geometry,
                                    "preprocessing": preprocessing,
                                    "text": text,
                                    "month": month_day[0],
                                    "day": month_day[1],
                                }
                            )
                    strict = _parse_full_year_month_day_audit(text)
                    if strict is not None:
                        strict_dates.add(strict)
    if (
        len(all_month_days) != 1
        or {item["model"] for item in observations} != {"mobile", "server"}
        or {item["geometry"] for item in observations} != {"紧凑区域", "宽区域"}
        or len(strict_dates) != 1
    ):
        return None
    month, day = next(iter(all_month_days))
    discarded = next(iter(strict_dates))
    candidate = date(required.year, month, day)
    business_start = max(creation, tracking)
    if not (
        (discarded.month, discarded.day) == (month, day)
        and discarded.year != candidate.year
        and (creation - discarded).days >= 365
        and business_start <= candidate <= required
        and (candidate - creation).days <= 7
        and (candidate - tracking).days <= 7
    ):
        return None
    return {
        "date": candidate,
        "discarded_strict": discarded,
        "support": observations,
    }


def _missing_month_day_component_prefilter_from_artifacts(
    artifacts: list[dict],
) -> dict | None:
    """Find one OCR-owned day with a partial year but no usable month.

    This is only a probe gate. It never creates a date and deliberately does
    not receive the printed required date. Mobile and Server must both expose
    the same explicit day after the printed ``月`` unit, and the evidence must
    span tight and wide geometries. Complete literal dates are rejected.
    """
    primary = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if primary is None or (
        "vision" not in str(primary.get("ocr_backend", "")).lower()
        or "paddle" not in str(primary.get("secondary_ocr_backend", "")).lower()
    ):
        return None
    day_support: list[dict] = []
    partial_year_models: set[str] = set()
    strict_dates: set[date] = set()
    for artifact in artifacts:
        geometry = str(artifact.get("variant", ""))
        if geometry not in {"紧凑区域", "宽区域"}:
            continue
        date_line_backend = str(artifact.get("date_line_ocr_backend", ""))
        for group_name in (
            "ocr_variants",
            "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for evidence in artifact.get(group_name) or []:
                preprocessing = str(evidence.get("preprocessing", ""))
                if "Server" in preprocessing:
                    model = "server"
                elif (
                    "Mobile" in preprocessing
                    or group_name == "secondary_ocr_variants"
                    or                     (
                        group_name == "date_line_ocr_variants"
                        and _is_lightweight_line_backend(date_line_backend)
                    )
                ):
                    model = "mobile"
                else:
                    model = ""
                for raw in evidence.get("ocr_texts") or []:
                    text = re.sub(r"\s+", "", str(raw))
                    strict = _parse_full_year_month_day_audit(text)
                    if strict is not None:
                        strict_dates.add(strict)
                    partial_year = re.search(r"(?<!\d)(20\d?)年", text)
                    if partial_year is not None and model:
                        partial_year_models.add(model)
                    day_match = re.search(r"月(\d{1,2})日(?!\d)", text)
                    if day_match is None or not model:
                        continue
                    day = int(day_match.group(1))
                    if 1 <= day <= 31:
                        day_support.append(
                            {
                                "model": model,
                                "geometry": geometry,
                                "preprocessing": preprocessing,
                                "text": str(raw),
                                "day": day,
                            }
                        )
    days = {item["day"] for item in day_support}
    if (
        strict_dates
        or len(days) != 1
        or {item["model"] for item in day_support} != {"mobile", "server"}
        or {item["geometry"] for item in day_support} != {"紧凑区域", "宽区域"}
        or partial_year_models != {"mobile", "server"}
        or len(day_support) < 3
    ):
        return None
    return {"day": next(iter(days)), "support": day_support}


def _partial_year_missing_month_business_consensus_from_artifacts(
    artifacts: list[dict],
    required_text: str,
    creation_text: str,
    tracking_text: str,
    slot_variants: list[dict] | None = None,
) -> dict | None:
    """Compose a missing-month date from independent OCR component slots.

    The day comes from cross-model/cross-geometry whole-line evidence. Paddle
    Mobile must repeat ``N月`` in the wider month context before and after line
    removal, while Paddle Server repeats the same digit in the narrow month
    slot. Only the year is supplied by three agreeing business dates. The OCR
    components must reconstruct the printed required date inside a three-day
    outbound window; no complete OCR date may coexist with this route.
    """
    prefilter = _missing_month_day_component_prefilter_from_artifacts(artifacts)
    required = parse_date(required_text)
    creation = parse_date(creation_text)
    tracking_match = re.search(r"W(20\d{2})(\d{2})(\d{2})", tracking_text)
    tracking = parse_date("-".join(tracking_match.groups()) if tracking_match else "")
    if (
        prefilter is None
        or required is None
        or creation is None
        or tracking is None
        or len({required.year, creation.year, tracking.year}) != 1
    ):
        return None
    tight = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        None,
    )
    if tight is None:
        return None
    variants = list(
        slot_variants
        if slot_variants is not None
        else tight.get("date_slot_ocr_variants") or []
    )
    expected_preprocessings = {
        "最大通道去彩色",
        "最大通道去彩色并去横线",
    }
    server_month_cells: dict[str, set[int]] = {}
    mobile_month_cells: dict[str, set[int]] = {}
    for variant in variants:
        slot = str(variant.get("slot", ""))
        model = str(variant.get("model", ""))
        preprocessing = str(variant.get("preprocessing", ""))
        if preprocessing not in expected_preprocessings:
            continue
        if slot == "月份数字窄槽" and model == "server":
            values = {
                parsed
                for text in variant.get("ocr_texts") or []
                if (parsed := _parse_date_slot_digit(str(text), maximum=12)) is not None
            }
            server_month_cells[preprocessing] = values
        elif slot == "月份上下文槽位" and model == "mobile":
            values: set[int] = set()
            for raw in variant.get("ocr_texts") or []:
                match = re.search(r"(?<!\d)(\d{1,2})月", re.sub(r"\s+", "", str(raw)))
                if match is not None and 1 <= int(match.group(1)) <= 12:
                    values.add(int(match.group(1)))
            mobile_month_cells[preprocessing] = values
    if (
        set(server_month_cells) != expected_preprocessings
        or set(mobile_month_cells) != expected_preprocessings
        or any(len(values) != 1 for values in server_month_cells.values())
        or any(len(values) != 1 for values in mobile_month_cells.values())
    ):
        return None
    months = {
        value
        for values in (*server_month_cells.values(), *mobile_month_cells.values())
        for value in values
    }
    if len(months) != 1:
        return None
    month = next(iter(months))
    try:
        candidate = date(required.year, month, prefilter["day"])
    except ValueError:
        return None
    business_start = max(creation, tracking)
    if not (
        candidate == required
        and business_start <= candidate
        and (candidate - creation).days <= 3
        and (candidate - tracking).days <= 3
    ):
        return None
    return {
        "date": candidate,
        "day_support": prefilter["support"],
        "month": month,
        "month_support": {
            "mobile_context": {
                key: sorted(values) for key, values in mobile_month_cells.items()
            },
            "server_digits": {
                key: sorted(values) for key, values in server_month_cells.items()
            },
        },
    }


def _partial_year_day_before_audit_from_artifacts(
    artifacts: list[dict],
    required_text: str,
    creation_text: str,
) -> dict | None:
    """Return a manual-only previous-day suggestion from four OCR cells.

    Mobile and Server must independently repeat the same explicit month/day in
    both tight and wide maximum-channel crops. Every cell must contain only a
    damaged two/three-digit ``20…年`` token—no complete date—and the resulting
    two-digit day must be exactly one day before the printed requirement and
    not before the outbound creation date. This never creates a reliable
    automatic decision and is disabled outside macOS Vision + Paddle Hybrid.
    """
    required = parse_date(required_text)
    creation = parse_date(creation_text)
    if required is None or creation is None or required.year != creation.year:
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
            if any(parse_date(text) is not None for text in texts):
                return None
            values = {
                parsed
                for text in texts
                if (parsed := _parse_partial_year_month_day_audit(text, required))
                is not None
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
    candidates = {next(iter(cells[cell])) for cell in required_cells}
    if len(candidates) != 1:
        return None
    candidate = next(iter(candidates))
    if not (
        candidate.year == required.year
        and candidate.month == required.month
        and candidate.day >= 10
        and required.day >= 10
        and (required - candidate).days == 1
        and candidate >= creation
    ):
        return None
    return {"date": candidate, "support": support}


def _collect_business_rejected_date_evidence(
    rows: list[TextObservation],
    required_text: str,
    creation_text: str,
    tracking_text: str = "",
) -> list[dict]:
    """Preserve every OCR date rejected by the outbound-date lower bound."""
    required = parse_date(required_text)
    grouped: dict[date, set[str]] = {}
    lower_bound = None
    for row in rows:
        parsed = parse_date(row.text)
        if parsed is None and required is not None:
            parsed = parse_receipt_date(row.text, required)
        if parsed is None:
            continue
        _, rejected, candidate_lower_bound = _reject_date_before_creation(
            parsed, creation_text, tracking_text
        )
        lower_bound = candidate_lower_bound or lower_bound
        if rejected is not None:
            grouped.setdefault(rejected, set()).add(str(row.text))
    return [
        {
            "value": value.isoformat(),
            "ocr_texts": sorted(texts),
            "reason": (
                f"早于最早出库业务日期 {lower_bound.isoformat()}"
                if lower_bound
                else "早于最早出库业务日期"
            ),
        }
        for value, texts in sorted(grouped.items())
    ]


def _reject_date_before_creation(
    actual_date, creation_text: str, tracking_text: str = ""
):
    """Reject dates before the earliest documented outbound business date.

    A receipt can be reprinted after delivery, so the visible creation date is
    not always the original outbound date. Samsung waybill numbers embed the
    dispatch date (``WYYYYMMDD-...``); when both are present, use the earlier
    one as the lower bound while leaving the printed field value unchanged.
    """
    creation_date = parse_date(creation_text)
    tracking_match = re.search(r"W(20\d{2})(\d{2})(\d{2})", tracking_text)
    tracking_date = parse_date(
        "-".join(tracking_match.groups()) if tracking_match else ""
    )
    business_dates = [value for value in (creation_date, tracking_date) if value]
    lower_bound = min(business_dates) if business_dates else None
    if actual_date and lower_bound and actual_date < lower_bound:
        return None, actual_date, lower_bound
    return actual_date, None, lower_bound
