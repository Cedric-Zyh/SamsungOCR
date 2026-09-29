"""Existing supporting-evidence gates, separate from provider calls."""
from receipt_ocr.domain.parsing import parse_date, parse_receipt_date


def accept_secondary(run, region, rows):
    if region.audit_only:
        return []
    return _matching_support(run, rows)


def accept_line(run, rows):
    return _matching_support(run, rows) if run.secondary_ocr_backend else list(rows)


def _matching_support(run, rows):
    required = parse_date(run.required_text)
    return [row for row in rows if
            (required is not None and parse_receipt_date(row.text, required) == required)
            or (run.allow_strict_date_without_requirement and required is None
                and parse_date(row.text) is not None)]
