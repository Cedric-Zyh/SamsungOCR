"""Parse literal date fragments without applying recognition verdicts."""

from __future__ import annotations
import re
from datetime import date
from .parser import parse_date, parse_receipt_date
from .ocr_types import TextObservation


_DATE_ONLY_TRANSLATION = str.maketrans(
    {
        "Ｏ": "0",
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "O": "0",
        "o": "0",
        "〇": "0",
    }
)


def normalize_date_only_text(text: str) -> str:
    """Keep only date characters from local OCR output.

    This is intentionally a light normalization layer, not a date guesser:
    it removes stamp/label text, keeps OCR-owned digits and ``年月日``, and
    converts an explicit numeric date separator into the same unit form used
    by the date parser.  Missing components remain missing.
    """
    compact = re.sub(r"\s+", "", str(text or "")).translate(_DATE_ONLY_TRANSLATION)
    # Keep the units and numeric separators long enough to normalize a common
    # local-OCR form such as ``盖章2026-02-06`` after the non-date prefix is
    # removed below.
    filtered = "".join(
        char for char in compact if char.isdigit() or char in "年月日-./"
    )
    full_numeric = re.search(
        r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)",
        filtered,
    )
    if full_numeric:
        year, month, day = full_numeric.groups()
        return f"{year}年{int(month)}月{int(day)}日"
    return filtered


def normalize_date_only_rows(rows: list[TextObservation]) -> list[TextObservation]:
    """Return OCR observations whose text is restricted to date characters."""
    normalized: list[TextObservation] = []
    for row in rows:
        text = normalize_date_only_text(row.text)
        if not text:
            continue
        normalized.append(
            type(row)(
                text=text,
                confidence=row.confidence,
                x=row.x,
                y=row.y,
                width=row.width,
                height=row.height,
            )
        )
    return normalized


def sanitize_date_artifacts(artifacts: list[dict]) -> list[dict]:
    """Restrict persisted local-date OCR text to digits and date units."""
    variant_keys = (
        "ocr_variants",
        "secondary_ocr_variants",
        "date_line_ocr_variants",
        "date_line_display_ocr_variants",
        "date_slot_ocr_variants",
        "date_slot_day_ocr_variants",
        "date_slot_day_inner_ocr_variants",
    )
    for artifact in artifacts:
        artifact["ocr_texts"] = [
            value
            for value in (
                normalize_date_only_text(text)
                for text in artifact.get("ocr_texts") or []
            )
            if value
        ]
        rows = []
        for row in artifact.get("decision_rows") or []:
            if not isinstance(row, dict):
                continue
            value = normalize_date_only_text(row.get("text", ""))
            if not value:
                continue
            copied = dict(row)
            copied["text"] = value
            rows.append(copied)
        if "decision_rows" in artifact:
            artifact["decision_rows"] = rows
        for key in variant_keys:
            for variant in artifact.get(key) or []:
                variant["ocr_texts"] = [
                    value
                    for value in (
                        normalize_date_only_text(text)
                        for text in variant.get("ocr_texts") or []
                    )
                    if value
                ]
                if "accepted_texts" in variant:
                    variant["accepted_texts"] = [
                        value
                        for value in (
                            normalize_date_only_text(text)
                            for text in variant.get("accepted_texts") or []
                        )
                        if value
                    ]
    return artifacts


def _trailing_numeric_month_day(text: str, required) -> bool:
    """Return whether OCR safely exposes the required trailing ``M.D``.

    A four-digit year, when present, remains authoritative and must agree.
    Short/noisy leading digits are deliberately ignored only here, where a
    second OCR model must independently produce the same month/day before the
    analyzer constructs a normalized date observation.
    """
    month_day = _explicit_trailing_numeric_month_day(text, required.year)
    return month_day == (required.month, required.day)


def _conflicting_receipt_dates(rows: list[TextObservation], required) -> set[date]:
    """Return parseable local candidates that contradict the target date."""
    return {
        parsed
        for row in rows
        if (parsed := parse_receipt_date(row.text, required)) is not None
        and parsed != required
    }


def _mobile_component_supports_date(text: str, candidate: date) -> bool:
    """Match OCR-owned year/month/day components without requirement repair."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if parse_date(compact) is not None:
        return False
    patterns = (
        r"(?<!\d)(\d{3})年(\d{1,2})月(\d{1,2})日?(?!\d)",
        r"(?<!\d)(20\d{2})(\d{1,2})月(\d{1,2})日?(?!\d)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            observed_year, raw_month, raw_day = match.groups()
            month, day = int(raw_month), int(raw_day)
            try:
                date(candidate.year, month, day)
            except ValueError:
                continue
            if (month, day) != (candidate.month, candidate.day):
                continue
            if len(observed_year) == 4:
                if int(observed_year) == candidate.year:
                    return True
                continue
            position = 0
            for char in observed_year:
                offset = str(candidate.year).find(char, position)
                if offset < 0:
                    break
                position = offset + 1
            else:
                return True
    return False


def _parse_server_audit_candidate(text: str, required):
    """Parse a low-confidence Server candidate without changing its month.

    The general receipt parser may inherit the required month when OCR only
    exposes a day. That is useful for established cross-model paths, but a
    single-model enlarged audit image must not turn literal ``1月5日`` or
    invalid ``0月9日`` into the required August date.
    """
    strict = parse_date(text)
    if strict is not None:
        return strict
    repaired = parse_receipt_date(text, required)
    if repaired is None:
        return None
    compact = re.sub(r"\s+", "", text)
    month_token = re.search(r"(\d{1,4})月", compact)
    if month_token:
        explicit_month = int(month_token.group(1))
        if not 1 <= explicit_month <= 12:
            return None
        if repaired.month != explicit_month:
            return None
    return repaired


def _year_month_prefix(text: str) -> tuple[int, int] | None:
    """Parse an explicit four-digit year/month prefix with a missing day."""
    compact = re.sub(r"\s+", "", str(text or ""))
    match = re.search(
        r"(?<!\d)(20\d{2})(?:年|[./-])(\d{1,2})(?:月|[./-])(?!\d)",
        compact,
    )
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return (year, month) if 1 <= month <= 12 else None


def _partial_year_month_day(
    text: str,
    candidate: date,
) -> tuple[int, int] | None:
    """Parse M/D when a three-digit OCR year is a subsequence of the truth."""
    compact = re.sub(r"\s+", "", str(text or ""))
    match = re.search(
        r"(?<!\d)(\d{3})(?:年|[./-])(\d{1,2})(?:月|[./-])" r"(\d{1,2})日?(?!\d)",
        compact,
    )
    if not match:
        return None
    observed_year = match.group(1)
    expected_year = str(candidate.year)
    position = 0
    for char in observed_year:
        offset = expected_year.find(char, position)
        if offset < 0:
            return None
        position = offset + 1
    month, day = int(match.group(2)), int(match.group(3))
    try:
        date(candidate.year, month, day)
    except ValueError:
        return None
    return month, day


def _parse_adaptive_day_slot(text: str) -> int | None:
    """Parse an isolated day while allowing the left printed month unit."""
    compact = re.sub(r"\s+", "", str(text))
    match = re.fullmatch(r"(?:月)?(\d{1,2})日", compact)
    if match is None:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 31 else None


def _parse_compact_full_date_audit_candidate(text: str) -> date | None:
    """Parse a complete YYYYMMDD audit reading with a missing separator.

    The terminal ``日`` and all eight digits must be present in the OCR text.
    No component is inherited from the required date, keeping this narrower
    than the ordinary receipt-date repair path.
    """
    compact = re.sub(r"\s+", "", text.replace("O", "0").replace("o", "0"))
    match = re.search(r"(?<!\d)(20\d{2})(?:年)?(\d{2})(\d{2})日(?!\d)", compact)
    if not match:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _parse_missing_year_separator_full_date(text: str) -> date | None:
    """Parse ``YYYYM月D日`` only when every date component is literal.

    A real handwritten row can lose the printed ``年`` while retaining the
    complete four-digit year, month unit, day unit and every digit (for example
    ``20256月16日``).  This parser does not repair a year or borrow a component
    from the requested date.  The narrow production route additionally needs
    a Server strict date and a four-cell day-only crop consensus.
    """
    compact = re.sub(r"\s+", "", str(text).replace("O", "0").replace("o", "0"))
    match = re.search(r"(?<!\d)(20\d{2})(\d{1,2})月(\d{1,2})日(?!\d)", compact)
    if not match:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _explicit_trailing_numeric_month_day(
    text: str,
    expected_year: int,
) -> tuple[int, int] | None:
    """Extract an explicit trailing month/day while treating year carefully."""
    compact = re.sub(r"\s", "", text.replace("O", "0").replace("o", "0"))
    explicit_year = re.search(r"(?<!\d)(\d{4})(?!\d)", compact)
    if explicit_year and int(explicit_year.group(1)) != expected_year:
        return None
    match = re.search(r"(\d{1,2})(?:[./-]|月)(\d{1,2})日?\D*$", compact)
    if not match:
        return None
    try:
        month, day = int(match.group(1)), int(match.group(2))
        date(expected_year, month, day)
    except ValueError:
        return None
    return month, day


def _parse_date_slot_year(text: str) -> int | None:
    """Return only a complete OCR-owned four-digit year before ``年``."""
    compact = re.sub(r"\s+", "", text.replace("O", "0").replace("o", "0"))
    match = re.search(r"(?<!\d)(20\d{2})年", compact)
    return int(match.group(1)) if match else None


def _parse_date_slot_month_day(text: str) -> tuple[int, int] | None:
    """Return an explicit month/day pair; never inherit either component."""
    compact = re.sub(r"\s+", "", text.replace("O", "0").replace("o", "0"))
    match = re.search(r"(\d{1,2})(?:[./-]|月)(\d{1,2})(?:日)?", compact)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    try:
        date(2000, month, day)
    except ValueError:
        return None
    return month, day


def _parse_date_slot_digit(text: str, *, maximum: int) -> int | None:
    """Return one OCR-owned numeric slot value without contextual repair."""
    compact = re.sub(r"\s+", "", str(text).replace("O", "0").replace("o", "0"))
    if not re.fullmatch(r"\d{1,2}", compact):
        return None
    value = int(compact)
    return value if 1 <= value <= maximum else None


def _parse_full_year_month_day_audit(text: str) -> date | None:
    """Parse literal year/month/day digits even when ``日`` is misread.

    This is intentionally narrower than :func:`parse_receipt_date`: all date
    digits and the printed ``年/月`` separators must exist in the OCR text.
    The terminal unit may be ``日`` or the common same-row confusion ``月``;
    no component is borrowed from the required-delivery date.
    """
    compact = re.sub(r"\s+", "", str(text).replace("O", "0").replace("o", "0"))
    match = re.search(
        r"(?<!\d)(20\d{2})年(\d{1,2})月(\d{1,2})(?:日|月)?(?!\d)",
        compact,
    )
    if not match:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _parse_partial_year_month_day_audit(
    text: str,
    required: date,
) -> date | None:
    """Parse an explicit month/day whose handwritten year lost 1–2 digits.

    This parser is deliberately audit-only. It requires the literal ``年/月/日``
    structure, a two- or three-digit year token beginning with ``20``, and an
    explicit month and day. Only the missing year suffix is inherited from the
    printed required date; complete four-digit years use the stricter parsers.
    """
    compact = re.sub(r"\s+", "", str(text).replace("O", "0").replace("o", "0"))
    match = re.search(r"(?<!\d)(20\d?)年(\d{1,2})月(\d{1,2})日(?!\d)", compact)
    if not match or len(match.group(1)) not in {2, 3}:
        return None
    try:
        return date(required.year, int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _is_date_audit_only_preprocessing(preprocessing: str) -> bool:
    """Identify low-weight date variants that are saved for review only."""
    return any(
        marker in preprocessing
        for marker in (
            "Server 大模型复核不一致日期",
            "Server 大模型低置信度候选",
            "人工候选",
        )
    )


def _parse_full_year_missing_month_day(text: str) -> tuple[int, int] | None:
    """Parse ``2025年月11日`` without borrowing the missing month."""
    compact = re.sub(r"\s+", "", str(text or ""))
    match = re.search(r"(?<!\d)(20\d{2})年?月(\d{1,2})日?(?!\d)", compact)
    if not match:
        return None
    year, day = (int(value) for value in match.groups())
    if not 1 <= day <= 31:
        return None
    return year, day
