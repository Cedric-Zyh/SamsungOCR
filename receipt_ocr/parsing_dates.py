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


def find_receipt_date(
    rows: list[TextObservation],
    required_text: str = "",
) -> tuple[date | None, TextObservation | None]:
    required = parse_date(required_text)
    candidates: dict[date, list[tuple[float, TextObservation]]] = {}
    for row in rows:
        found = parse_receipt_date(row.text, required)
        if not found:
            continue
        # The receiving date is on the right side of the signature table.
        if 0.50 <= row.y <= 0.69 and row.x >= 0.58:
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
