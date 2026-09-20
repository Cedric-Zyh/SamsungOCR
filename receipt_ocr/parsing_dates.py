"""Date text parsing, candidate comparison and confidence from OCR observations."""

from __future__ import annotations

import re
from datetime import date

from .ocr_types import TextObservation
from .parsing_constants import DATE_PATTERN


def parse_date(text: str) -> date | None:
    cleaned = text.replace("O", "0").replace("o", "0")
    match = DATE_PATTERN.search(cleaned)
    if not match:
        return None
    try:
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None


def parse_receipt_date(text: str, required: date | None) -> date | None:
    strict = parse_date(text)
    if strict and (required is None or strict.year == required.year):
        return strict
    if strict and required is not None and strict.year != required.year:
        return None
    if required is None:
        return None
    compact = re.sub(r"\s", "", text.replace("O", "0").replace("o", "0"))
    explicit_year = re.search(r"(?<!\d)(\d{4})年", compact)
    if explicit_year and int(explicit_year.group(1)) != required.year:
        # A visibly present but different/invalid four-digit year is contrary
        # evidence, not a missing year that may inherit the printed required
        # year. For example, ``1105年2月23`` must not become 2025-02-23.
        return None
    # A longer numeric run immediately before ``年`` is a malformed year,
    # not a missing year. Reject it instead of silently inheriting the
    # required year (for example, ``21127年9月5日`` must not become
    # ``2026-09-05``).
    if re.search(r"(?<!\d)\d{5,}年", compact):
        return None
    compact = re.sub(r"(?<=\d)[司曰目可「口T丁川]$", "日", compact, flags=re.IGNORECASE)
    # Handwritten dates at the right edge often lose the final “日”.  A full
    # year/month/day expression still provides independent evidence; short
    # fragments without both 年 and 月 remain rejected.
    if "日" not in compact and re.search(r"年[^0-9]{0,2}\d{1,2}月[^0-9]{0,2}\d{1,2}$", compact):
        compact += "日"
    if "日" not in compact:
        return None
    day_match = re.search(r"月[^0-9]{0,2}(\d{1,2})日", compact)
    if not day_match:
        return None
    day = int(day_match.group(1))
    # Read the digits immediately before this day expression's 月, even when
    # OCR omitted the year. An explicit ``6月11日`` must not inherit May from
    # the printed requirement. A longer run can include a damaged year, as in
    # ``20252月23日`` or ``206月19日``; only a matching business-year prefix and
    # one unambiguous, valid trailing month can repair that missing separator.
    month_match = re.search(r"(\d+)$", compact[:day_match.start()])
    month = int(month_match.group(1)) if month_match else required.month
    if month_match and len(month_match.group(1)) > 2:
        digits = month_match.group(1)
        months = {
            int(digits[length:])
            for length in (4, 3, 2)
            if digits.startswith(str(required.year)[:length])
            and 1 <= len(digits[length:]) <= 2
            and 1 <= int(digits[length:]) <= 12
        }
        if len(months) != 1:
            return None
        month = months.pop()
    try:
        return date(required.year, month, day)
    except ValueError:
        return None


def extract_date_components(rows: list[TextObservation]) -> dict[str, int | None]:
    """Extract OCR-owned year/month/day components without filling gaps.

    The normal date parser intentionally returns ``None`` for an incomplete
    date.  The review UI still needs to show useful partial evidence, so this
    helper keeps independently visible components from the tight crop while
    refusing to borrow any missing value from the requested delivery date.
    Conflicting values are left blank rather than guessed.
    """
    observed: dict[str, set[int]] = {"year": set(), "month": set(), "day": set()}
    invalid_observed = {"year": False, "month": False, "day": False}
    for row in rows:
        compact = re.sub(r"\s+", "", str(row.text or "")).replace("O", "0").replace("o", "0")
        if not compact:
            continue
        strict = parse_date(compact)
        if strict is not None:
            observed["year"].add(strict.year)
            observed["month"].add(strict.month)
            observed["day"].add(strict.day)
            continue

        year_matches = re.findall(r"(?<!\d)(\d{4})年", compact)
        if not year_matches:
            year_matches = re.findall(r"(?<!\d)(20\d{2})(?!\d)", compact)
        observed["year"].update(int(value) for value in year_matches)

        month_matches = re.findall(r"(?:年|[./-])(\d{1,2})月", compact)
        month_matches += re.findall(r"(?<!\d)(\d{1,2})月", compact)
        observed["month"].update(
            int(value) for value in month_matches if 1 <= int(value) <= 12
        )

        paired_month_days = re.findall(
            r"(\d{1,2})月(?:[^0-9]{0,2})(\d{1,2})(?:日|$)", compact
        )
        # Only keep the day when its preceding month is itself valid.  This
        # prevents malformed OCR such as ``26年52月3日`` from contributing a
        # false day ``3`` to an otherwise incomplete date.
        day_matches = [
            day
            for month, day in paired_month_days
            if 1 <= int(month) <= 12
        ]
        if any(
            1 <= int(month) <= 12 and not 1 <= int(day) <= 31
            for month, day in paired_month_days
        ):
            # A row that explicitly says ``...月0日`` is contradictory
            # evidence for the day, not an absent day that can be combined
            # with another derivative reading.
            invalid_observed["day"] = True
        # A standalone ``2日`` is useful when the month is absent, but never
        # borrow a day from a row that already contains an invalid month form.
        if not paired_month_days and "月" not in compact:
            day_matches = re.findall(r"(?<!\d)(\d{1,2})日", compact)
        observed["day"].update(
            int(value) for value in day_matches if 1 <= int(value) <= 31
        )

    return {
        name: (
            next(iter(values))
            if len(values) == 1 and not invalid_observed[name]
            else None
        )
        for name, values in observed.items()
    }


def format_partial_date(components: dict[str, int | None]) -> str:
    """Format OCR-owned date components, keeping missing slots visibly blank."""
    year = components.get("year")
    month = components.get("month")
    day = components.get("day")
    return "-".join(
        (
            f"{int(year):04d}" if year is not None else "____",
            f"{int(month):02d}" if month is not None else "__",
            f"{int(day):02d}" if day is not None else "__",
        )
    )


def compare_partial_date_components(
    required_text: str, components: dict[str, int | None]
) -> dict:
    """Compare only the components that OCR actually observed.

    An incomplete date is never marked as a reliable match.  Known component
    conflicts are surfaced, while missing components remain explicitly blank.
    """
    required = parse_date(required_text)
    display = format_partial_date(components)
    names = {"year": "年", "month": "月", "day": "日"}
    missing = [names[key] for key in ("year", "month", "day") if components.get(key) is None]
    conflicts = []
    if required is not None:
        for key in ("year", "month", "day"):
            value = components.get(key)
            if value is not None and value != getattr(required, key):
                conflicts.append(names[key])
    if not any(value is not None for value in components.values()):
        return {
            "required": required.isoformat() if required else "",
            "actual": "",
            "actual_display": "",
            "actual_components": components,
            "status": "未识别",
            "message": "未识别到签收日期",
            "confidence": 0,
            "reliable": False,
        }
    message = "签收日期部分识别"
    if missing:
        message += f"，缺少：{'、'.join(missing)}"
    if conflicts:
        message += f"；已识别的{'、'.join(conflicts)}与要求到货不一致"
    return {
        "required": required.isoformat() if required else "",
        "actual": "",
        "actual_display": display,
        "actual_components": components,
        "status": "部分识别",
        "message": message,
        "confidence": 0,
        "reliable": False,
    }


# The receiving date sits a fixed distance below the signature-requirement row.
# Measured over the ground-truth samples the offset is +0.078~+0.084 with no
# exception (7266702320 +0.082, 7266933626 +0.084, 7267130988 +0.079,
# 7267237228 +0.083, 7267446210 +0.083, 7330644044 +0.078).  What varies is
# where that footer sits on the page: 0.470~0.472 on the usual layout, 0.736 on
# 7330644044.  The absolute band below is therefore a footer-relative band in
# disguise -- it equals ``anchor + 0.03 .. anchor + 0.22`` for the usual
# ``anchor`` of 0.471.  Reading it as an absolute page fraction is what made
# every correctly recognised date on a low-footer layout come back 未识别.
DATE_BAND_TOP = 0.50
DATE_BAND_BOTTOM = 0.69
DATE_BAND_RIGHT = 0.58
REFERENCE_ANCHOR_Y = 0.471
ANCHOR_OFFSET_TOP = DATE_BAND_TOP - REFERENCE_ANCHOR_Y
ANCHOR_OFFSET_BOTTOM = DATE_BAND_BOTTOM - REFERENCE_ANCHOR_Y


def find_receipt_date(
    rows: list[TextObservation],
    required_text: str = "",
    *,
    anchor_y: float | None = None,
) -> tuple[date | None, TextObservation | None]:
    """Locate the receiving date inside the signature footer.

    ``anchor_y`` is the y of the signature-requirement row for this document.
    Passing it lets the band follow a footer that does not sit at the usual
    height; the result is a strict superset of the absolute band, so nothing
    that used to be accepted is dropped.  Callers that have no anchor (saved
    evidence audits) leave it unset and keep the historical behaviour.
    """
    required = parse_date(required_text)
    candidates: dict[date, list[tuple[float, TextObservation]]] = {}
    for row in rows:
        found = parse_receipt_date(row.text, required)
        if not found:
            continue
        # The receiving date is on the right side of the signature table.
        if row.x < DATE_BAND_RIGHT:
            continue
        in_band = DATE_BAND_TOP <= row.y <= DATE_BAND_BOTTOM
        if not in_band and anchor_y is not None:
            offset = row.y - float(anchor_y)
            in_band = ANCHOR_OFFSET_TOP <= offset <= ANCHOR_OFFSET_BOTTOM
        if not in_band:
            continue
        score = row.confidence + row.x + (0.2 if "年" in row.text else 0)
        candidates.setdefault(found, []).append((score, row))
    if not candidates:
        return None, None
    ranked: list[tuple[float, date, TextObservation]] = []
    for found, evidence in candidates.items():
        best_score, best_row = max(evidence, key=lambda item: item[0])
        # Independent OCR variants are stronger than one locally confident
        # misread. Matching the required date is a prior, never a fabricated
        # value: the bonus applies only when OCR actually produced that date.
        consensus_bonus = min(0.36, 0.12 * (len(evidence) - 1))
        required_bonus = 0.35 if required is not None and found == required else 0.0
        ranked.append((best_score + consensus_bonus + required_bonus, found, best_row))
    _, found, row = max(ranked, key=lambda item: item[0])
    return found, row


def compare_dates(required_text: str, actual: date | None) -> dict:
    required = parse_date(required_text)
    if required is None:
        status, message = "无法判断", "未识别到要求到货日期"
    elif actual is None:
        status, message = "未识别", "未识别到收货日期"
    elif required == actual:
        status, message = "匹配", "收货日期与要求到货日期一致"
    else:
        delta = (actual - required).days
        status = "不匹配"
        message = f"实际收货比要求日期{'晚' if delta > 0 else '早'} {abs(delta)} 天"
    return {
        "required": required.isoformat() if required else "",
        "actual": actual.isoformat() if actual else "",
        "status": status,
        "message": message,
    }


def estimate_date_confidence(
    rows: list[TextObservation],
    required_text: str,
    actual: date | None,
) -> float:
    if actual is None:
        return 0.0
    required = parse_date(required_text)
    evidence: list[tuple[float, bool]] = []
    for row in rows:
        parsed = parse_receipt_date(row.text, required)
        if parsed != actual:
            continue
        strict = parse_date(row.text) == actual
        evidence.append((float(row.confidence), strict))
    if not evidence:
        return 0.35
    score = max(confidence for confidence, _ in evidence)
    # A repaired candidate such as ``202年2月2日`` is ambiguous when the
    # printed requirement has a two-digit day: the OCR may have clipped 21,
    # 22 or 27 to a single 2.  Keep the displayed candidate, but never let
    # repeated partial readings turn it into an automatic pass/rejection.
    compact_rows = [
        re.sub(r"\s", "", row.text)
        for row in rows
        if parse_receipt_date(row.text, required) == actual
    ]
    ambiguous_single_day = bool(
        required is not None
        and required.day >= 10
        and actual.day < 10
        and not any(strict for _, strict in evidence)
        and compact_rows
        and all(not re.search(r"\d{4}[^\d]{0,2}\d{1,2}[^\d]{0,2}\d{2}", text) for text in compact_rows)
    )
    if ambiguous_single_day:
        return round(min(0.6, score), 3)
    # One month/day fragment that happens to equal the printed requirement is
    # not an independent receipt-date verdict.  Real handwriting can make 21
    # look like 25 (7284653895); the required date then acts as a dangerous
    # prior.  Keep the candidate visible, but require either a strict full
    # date or multiple accepted observations before automatic matching.
    if not any(strict for _, strict in evidence) and len(evidence) == 1:
        return round(min(0.68, score), 3)
    if any(strict for _, strict in evidence):
        score += 0.2
    if len(evidence) >= 2:
        score += 0.22
    elif evidence and not any(strict for _, strict in evidence):
        score += 0.08
    return round(min(1.0, score), 3)
