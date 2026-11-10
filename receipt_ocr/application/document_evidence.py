"""Pure selection and re-evaluation of live or saved provider evidence."""

from copy import deepcopy
from dataclasses import replace

from ..domain.results import StageOutcome
from ..domain.stages import STAGES
from ..domain.printed_field_selection import select_printed_fields
from ..domain.date_selection import select_date_variant


def stage_outcomes(results):
    return {stage: [StageOutcome.from_variant(stage, value) for value in results.get(stage, [])]
            for stage in STAGES}


def variant_payloads(outcomes):
    """Adapt to domain selectors that also accept the public JSON format."""
    return [outcome.to_variant(copy_payload=False) if isinstance(outcome, StageOutcome) else outcome
            for outcome in outcomes]


def selected_date_outcome(outcomes, *, required_date=None):
    """Apply JSON evidence-quality gates and retain the original outcome identity."""
    variants = variant_payloads(outcomes)
    selected = select_date_variant(variants, required_date=required_date)
    return next((outcome for outcome, variant in zip(outcomes, variants) if variant is selected), None)


def selected_document_fields(outcomes, previous_fields):
    fields = deepcopy(previous_fields or {})
    successful = [outcome for outcome in outcomes
                  if StageOutcome.from_variant("fields", outcome).has_result]
    if successful:
        fields.update(select_printed_fields(variant_payloads(successful))["fields"])
    return fields


def evaluate_document_evidence(results, previous_fields, config, *, recompare=False,
                               preserve_provider_checks=False):
    """Select the field basis and optionally replay comparisons, never OCR.

    A synchronous run already compared against the selected printed fields.
    A deferred response can replace those fields; its saved date/seal readings
    then use this same selection, without importing scheduling or persistence.
    """
    outcomes = stage_outcomes(results)
    fields = selected_document_fields(outcomes["fields"], previous_fields)
    if not recompare or preserve_provider_checks:
        return outcomes, fields
    from ..review import _review_date, _review_seal
    from ..recognition.seal.providers.qingtong import compare_qingtong_seal

    for stage in ("date", "seal"):
        for index, outcome in enumerate(outcomes[stage]):
            if not outcome.has_result or outcome.method == "danzhengtong":
                continue
            key = f"{stage}_check"
            check = outcome.payload.get(key, {})
            if stage == "date":
                updated = _review_date(check, fields, {"要求到货": check.get("required", "")}, {})
            elif check.get("requirement", "") == fields.get("签章要求", ""):
                continue
            elif outcome.method == "qingtong" and check.get("api"):
                updated = compare_qingtong_seal(fields.get("签章要求", ""), check["api"],
                    (config.get("acceptance") or {}).get("seal_match_mode", "any"))
            else:
                updated = _review_seal(check, fields, {"签章要求": check.get("requirement", "")}, {})
            payload = {**outcome.payload, key: updated}
            outcomes[stage][index] = replace(outcome, payload=payload)
    return outcomes, fields
