"""Collect bounded review-only and supplemental date evidence."""

from __future__ import annotations
from datetime import date
from .parser import (
    estimate_date_confidence,
    find_receipt_date,
    parse_date,
    parse_receipt_date,
)
from .ocr_types import TextObservation
from .date_fragments import (
    _partial_year_month_day,
    _year_month_prefix,
)


def _find_low_confidence_date_audit(
    rows: list[TextObservation],
) -> tuple[date | None, TextObservation | None]:
    """Expose strict audit dates without making them reliable.

    ``find_receipt_date`` intentionally accepts only the normal footer band.
    A far-below handwritten note therefore needs a separate path.  Require two
    strict observations of the same date and the audit confidence cap for the
    normal audit path.  A single observation is allowed only when it has the
    explicit ``0.25`` display-only cap applied by ``_select_display_only_date_audit_rows``;
    this returns a display/review candidate, never an automatic decision.
    """
    grouped: dict[date, list[TextObservation]] = {}
    for row in rows:
        if float(row.confidence) > 0.35:
            continue
        parsed = parse_date(row.text)
        if parsed is not None:
            grouped.setdefault(parsed, []).append(row)
    supported = [
        (parsed, evidence) for parsed, evidence in grouped.items() if len(evidence) >= 2
    ]
    if not supported:
        display_only = [
            (parsed, evidence)
            for parsed, evidence in grouped.items()
            if len(evidence) == 1 and float(evidence[0].confidence) <= 0.25
        ]
        if len(display_only) != 1:
            return None, None
        parsed, evidence = display_only[0]
        return parsed, evidence[0]
    parsed, evidence = max(
        supported,
        key=lambda item: (len(item[1]), max(row.confidence for row in item[1])),
    )
    return parsed, max(evidence, key=lambda row: row.confidence)


def _find_confirmed_far_lower_date(
    rows: list[TextObservation],
    artifacts: list[dict],
) -> tuple[date | None, TextObservation | None]:
    """Select only a far-below date carrying the strict cross-model marker."""
    candidates = {
        parsed
        for artifact in artifacts
        if (
            parsed := parse_date(
                str(artifact.get("far_lower_cross_model_candidate", ""))
            )
        )
        is not None
    }
    if len(candidates) != 1:
        return None, None
    candidate = next(iter(candidates))
    evidence = [
        row
        for row in rows
        if parse_date(row.text) == candidate and row.confidence > 0.35
    ]
    if len(evidence) < 2:
        return None, None
    return candidate, max(evidence, key=lambda row: row.confidence)


def _unique_server_mobile_otsu_candidate(
    server_texts: list[str],
    mobile_otsu_texts: list[str],
    existing_strict_texts: list[str],
) -> date | None:
    """Return one strict cross-model date for manual review only.

    The Server candidate must be unique, Mobile must independently produce
    the same complete date on the Otsu image, and no already parsed strict
    date may contradict it.  This helper deliberately does not use the
    required-delivery date as a repair source.
    """
    server_dates = {
        parsed for text in server_texts if (parsed := parse_date(text)) is not None
    }
    if len(server_dates) != 1:
        return None
    candidate = next(iter(server_dates))
    mobile_dates = {
        parsed for text in mobile_otsu_texts if (parsed := parse_date(text)) is not None
    }
    if mobile_dates != {candidate}:
        return None
    existing_dates = {
        parsed
        for text in existing_strict_texts
        if (parsed := parse_date(text)) is not None
    }
    if existing_dates and existing_dates != {candidate}:
        return None
    return candidate


def _repeated_strict_date_across_variants(
    variants: list[dict],
) -> date | None:
    """Return one strict date observed in at least two preprocessing views."""
    support: dict[date, set[str]] = {}
    for variant in variants:
        label = str(variant.get("preprocessing", ""))
        for text in variant.get("ocr_texts", []) or []:
            parsed = parse_date(str(text))
            if parsed is not None:
                support.setdefault(parsed, set()).add(label)
    repeated = [value for value, labels in support.items() if len(labels) >= 2]
    return repeated[0] if len(repeated) == 1 else None


def _cross_model_far_lower_strict_date(
    mobile_variants: list[dict],
    server_variants: list[dict],
    existing_texts: list[str],
) -> date | None:
    """Confirm one far-below strict date across models and crop geometries.

    Mobile reads the complete audit region while Server reads a narrower date
    line. Each model must repeat the same literal four-digit date in two
    preprocessing variants. Any other literal strict date vetoes promotion;
    the printed required date is never used for parsing or repair.
    """
    mobile = _repeated_strict_date_across_variants(mobile_variants)
    server = _repeated_strict_date_across_variants(server_variants)
    if mobile is None or server != mobile:
        return None
    existing = {
        parsed
        for text in existing_texts
        if (parsed := parse_date(str(text))) is not None
    }
    return mobile if not (existing - {mobile}) else None


def _cross_model_far_lower_complementary_date(
    mobile_region_variants: list[dict],
    server_line_variants: list[dict],
    padded_mobile_texts: list[str],
    padded_server_texts: list[str],
    existing_texts: list[str],
) -> date | None:
    """Confirm a border-clipped date through complementary OCR components.

    Mobile must repeat one literal full date in two whole-region views and in
    the independently cropped, right-padded line. Server must expose the same
    year/month before padding and the same month/day after padding. No printed
    required-date component is used to construct the candidate.
    """
    candidate = _repeated_strict_date_across_variants(mobile_region_variants)
    if candidate is None or not any(
        parse_date(str(text)) == candidate for text in padded_mobile_texts
    ):
        return None
    server_prefixes = {
        parsed
        for variant in server_line_variants
        for text in (variant.get("ocr_texts") or [])
        if (parsed := _year_month_prefix(str(text))) is not None
    }
    if (candidate.year, candidate.month) not in server_prefixes:
        return None
    server_suffixes = set()
    for text in padded_server_texts:
        parsed = parse_date(str(text))
        if parsed == candidate:
            server_suffixes.add((candidate.month, candidate.day))
            continue
        partial = _partial_year_month_day(str(text), candidate)
        if partial is not None:
            server_suffixes.add(partial)
    if (candidate.month, candidate.day) not in server_suffixes:
        return None
    existing = {
        parsed
        for text in (
            list(existing_texts) + list(padded_mobile_texts) + list(padded_server_texts)
        )
        if (parsed := parse_date(str(text))) is not None
    }
    return candidate if not (existing - {candidate}) else None


def _needs_low_confidence_date_audit(
    rows: list[TextObservation],
    required_text: str,
) -> bool:
    """Run strict component audits for empty or sole low-confidence matches.

    A partial date can already make ``find_receipt_date`` return the printed
    requirement, which previously skipped all fixed-slot verification.  Admit
    that case only when the result is still below the reliable threshold and
    no OCR-owned interpretation points to any other date.  The requirement is
    comparison-only; it never supplies a missing component to the later slot
    recognizers.
    """
    required = parse_date(required_text)
    actual, _ = find_receipt_date(rows, required_text)
    if actual is None:
        return True
    if required is None or actual != required:
        return False
    if estimate_date_confidence(rows, required_text, actual) >= 0.72:
        return False
    observed = {
        parsed
        for row in rows
        if (parsed := parse_receipt_date(row.text, required)) is not None
    }
    return bool(observed and observed <= {required})


def _select_display_only_date_audit_rows(
    rows: list[TextObservation],
    required_text: str,
    *,
    base_date: date | None,
) -> list[TextObservation]:
    """Keep human-review candidates without inflating decision evidence.

    Several preprocessings of one physical date line are not independent
    observations.  A same-year candidate therefore contributes at most one
    25% display row, and only when the original decision rows had no date.
    The established cross-year audit is different: two geometric crops may
    remain visible at the 35% audit cap so ``_find_low_confidence_date_audit``
    can show the anomalous year, but that selector is explicitly review-only.
    """
    required = parse_date(required_text)
    strict_cross_year: dict[date, list[TextObservation]] = {}
    if required is not None:
        for row in rows:
            parsed = parse_date(row.text)
            if parsed is not None and parsed.year != required.year:
                strict_cross_year.setdefault(parsed, []).append(row)
    if len(strict_cross_year) == 1:
        evidence = next(iter(strict_cross_year.values()))
        if len(evidence) >= 2:
            return [
                TextObservation(
                    text=row.text,
                    confidence=min(0.35, row.confidence),
                    x=row.x,
                    y=row.y,
                    width=row.width,
                    height=row.height,
                )
                for row in sorted(
                    evidence, key=lambda item: item.confidence, reverse=True
                )[:2]
            ]
    if base_date is not None:
        return []
    grouped: dict[date, list[TextObservation]] = {}
    for row in rows:
        parsed = parse_receipt_date(row.text, required)
        if parsed is not None:
            grouped.setdefault(parsed, []).append(row)
    if len(grouped) != 1:
        return []
    best = max(next(iter(grouped.values())), key=lambda row: row.confidence)
    return [
        TextObservation(
            text=best.text,
            confidence=min(0.25, best.confidence),
            x=best.x,
            y=best.y,
            width=best.width,
            height=best.height,
        )
    ]


def _cross_model_max_channel_mismatch_date(
    evidence: list[dict],
    all_rows: list[TextObservation],
    required: date,
) -> tuple[date, tuple[dict, dict]] | None:
    """Confirm one strict non-required date across models and geometries.

    Every component must be present in literal OCR text from the maximum-
    channel date-line view.  Mobile and Server must agree while using opposite
    tight/wide crops, and any other receipt-date interpretation vetoes the
    route.  The required-delivery date is comparison-only and never repairs a
    candidate.
    """
    pairs: dict[date, tuple[dict, dict]] = {}
    for mobile_item in evidence:
        candidate = mobile_item.get("date")
        if (
            mobile_item.get("model") != "mobile"
            or not isinstance(candidate, date)
            or candidate == required
        ):
            continue
        server_item = next(
            (
                item
                for item in evidence
                if item.get("model") == "server"
                and item.get("date") == candidate
                and item.get("tight") != mobile_item.get("tight")
            ),
            None,
        )
        if server_item is not None:
            pairs[candidate] = (mobile_item, server_item)
    if len(pairs) != 1:
        return None
    candidate, pair = next(iter(pairs.items()))
    observed = {
        parsed
        for row in all_rows
        if (parsed := parse_receipt_date(row.text, required)) is not None
    }
    if observed - {candidate}:
        return None
    return candidate, pair


def _max_channel_truncated_mismatch_candidate(
    evidence: list[dict],
    all_rows: list[TextObservation],
    required: date,
) -> tuple[date, list[dict]] | None:
    """Find a non-required date whose only strict conflict lost one day digit.

    This is only the first gate. The caller must independently recognize the
    fixed-template day slot before accepting anything. The complete candidate
    must occupy all four Mobile/Server × tight/wide maximum-channel cells, be
    within three days of the requested date, and have at most one other literal
    full-date interpretation. If present, that interpretation may
    differ only by reducing the two-digit day to one of its printed digits.
    Partial-year repairs are excluded because they are not literal full dates
    and often come from stamp-contaminated derivatives.
    """
    by_date: dict[date, dict[tuple[str, bool], dict]] = {}
    for item in evidence:
        candidate = item.get("date")
        model = str(item.get("model") or "")
        tight = item.get("tight")
        if (
            not isinstance(candidate, date)
            or candidate == required
            or model not in {"mobile", "server"}
            or not isinstance(tight, bool)
            or item.get("compact")
            or not any(
                parse_date(row.text) == candidate for row in item.get("rows", [])
            )
        ):
            continue
        by_date.setdefault(candidate, {})[(model, tight)] = item
    required_cells = {
        ("mobile", True),
        ("mobile", False),
        ("server", True),
        ("server", False),
    }
    candidates = [
        (candidate, cells)
        for candidate, cells in by_date.items()
        if set(cells) == required_cells
        and candidate.day >= 10
        and abs((candidate - required).days) <= 3
    ]
    if len(candidates) != 1:
        return None
    candidate, cells = candidates[0]
    strict_dates = {
        parsed for row in all_rows if (parsed := parse_date(row.text)) is not None
    }
    conflicts = strict_dates - {candidate}
    if len(conflicts) > 1:
        return None
    if conflicts:
        conflict = next(iter(conflicts))
        if (
            conflict.year != candidate.year
            or conflict.month != candidate.month
            or conflict.day not in {candidate.day // 10, candidate.day % 10}
        ):
            return None
    return candidate, list(cells.values())


def _cross_model_max_channel_required_with_truncated_conflict(
    evidence: list[dict],
    conflicts: set[date],
    required: date,
) -> list[dict] | None:
    """Confirm a required date despite one single-digit day truncation.

    The complete date must be independently present in at least three unique
    model-by-geometry cells, spanning Mobile/Server and tight/wide crops.  The
    only contradictory interpretation may keep the same year/month and reduce
    a two-digit day to one of its actually printed digits.  Compact repaired
    candidates, two-cell consensus, multiple conflicts and any month/year
    disagreement remain review-only.
    """
    if len(conflicts) != 1 or required.day < 10:
        return None
    conflict = next(iter(conflicts))
    if (
        conflict.year != required.year
        or conflict.month != required.month
        or conflict.day not in {required.day // 10, required.day % 10}
    ):
        return None
    by_cell: dict[tuple[str, bool], dict] = {}
    for item in evidence:
        model = str(item.get("model") or "")
        tight = item.get("tight")
        if (
            model not in {"mobile", "server"}
            or not isinstance(tight, bool)
            or item.get("compact")
            or not any(parse_date(row.text) == required for row in item.get("rows", []))
        ):
            continue
        by_cell[(model, tight)] = item
    cells = set(by_cell)
    if (
        len(cells) < 3
        or {model for model, _tight in cells} != {"mobile", "server"}
        or {tight for _model, tight in cells} != {False, True}
        or not any(
            left_model != right_model and left_tight != right_tight
            for left_model, left_tight in cells
            for right_model, right_tight in cells
        )
    ):
        return None
    return list(by_cell.values())
