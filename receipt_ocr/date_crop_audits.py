"""Run bounded supplemental OCR and cap unconfirmed audit observations."""

from .date_crop_state import DateCropRun
from .parser import find_receipt_date, parse_date
from .date_evidence import (
    _needs_low_confidence_date_audit,
    _select_display_only_date_audit_rows,
)
from .date_crop_line_audits import (
    _audit_server_date_lines,
    _expose_repeated_strict_audit,
    _audit_white_date_lines,
    _audit_otsu_date_lines,
)
from .date_crop_slots import probe_date_slots


def run_low_confidence_date_audit(run: DateCropRun) -> None:
    # If normal Hybrid evidence still produced no date, expose Server
    # recognizer candidates for human review. Partial required-year
    # repairs are capped at 0.45. A literal strict date with a
    # different year is capped at 0.35 and only becomes a displayed
    # audit candidate when the helper below sees it in at least two
    # geometric crops. Neither path can become an automatic verdict.
    low_confidence_audit_base_rows = list(run.output)
    run_low_confidence_date_audit = bool(
        run.secondary_ocr_backend == "paddle"
        and _needs_low_confidence_date_audit(
            low_confidence_audit_base_rows, run.required_text
        )
    )
    low_confidence_audit_output_start = len(run.output)
    if run_low_confidence_date_audit:
        required = parse_date(run.required_text)
        if required is not None:
            from .paddle_ocr import recognize_line

            server_strict_audit_texts: list[str] = []
            _audit_server_date_lines(
                run, required, recognize_line, server_strict_audit_texts
            )
            _expose_repeated_strict_audit(
                run, recognize_line, server_strict_audit_texts
            )
            _audit_white_date_lines(run, required, recognize_line)
            _audit_otsu_date_lines(run, recognize_line)
            probe_date_slots(run, low_confidence_audit_base_rows, recognize_line)

    if run_low_confidence_date_audit and not any(
        artifact.get("date_slot_reliable") for artifact in run.artifacts
    ):
        # Server/Otsu/autocontrast variants above are audit evidence,
        # not independent physical observations. Keep every OCR text
        # in the artifact history, but do not let several derivatives
        # of one line accumulate into an automatic verdict. If the
        # decision-grade rows had no date at all, retain only one
        # capped observation so the candidate remains visible for a
        # human reviewer.
        added_audit_rows = run.output[low_confidence_audit_output_start:]
        del run.output[low_confidence_audit_output_start:]
        base_date, _ = find_receipt_date(
            low_confidence_audit_base_rows, run.required_text
        )
        run.output.extend(
            _select_display_only_date_audit_rows(
                added_audit_rows,
                run.required_text,
                base_date=base_date,
            )
        )
