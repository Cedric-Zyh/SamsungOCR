"""Auditable company-name correction against all registered seal names.

The current receipt requirement is deliberately not a correction candidate.
Purpose text, station IDs and stamp numbers never enter this vocabulary.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import SequenceMatcher
import re

from receipt_ocr.domain.seal_name_terms import seal_name_terms, normalized_name

_NAMES = ContextVar("seal_company_name_vocabulary", default=())
MAX_NAME_EDITS = 2
MAX_BRANCH_EDITS = 1
MAX_EDGE_NOISE = 2
MIN_NAME_SIMILARITY = .85
_BRANCH_SUFFIX = re.compile(r'公司([\u3400-\u9fff]{1,18})$')


def _clean(text):
    return normalized_name(text)


def registered_company_names(standards):
    names = set()
    for standard in standards:
        # Registration/requirement wording is authoritative; its OCR reading
        # may contain exactly the error this vocabulary is meant to correct.
        terms = seal_name_terms(standard.get('requirement'))
        if terms['correction_supported']:
            names.add(terms['company'])
            names.add(terms['name'])
    return tuple(sorted(names))


@contextmanager
def company_name_correction_scope(standards):
    token = _NAMES.set(registered_company_names(standards))
    try:
        yield
    finally:
        _NAMES.reset(token)


def company_name_terms(requirement):
    return seal_name_terms(requirement)


def _distance(left, right, limit=MAX_NAME_EDITS):
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def correct_company_name(text, *, names=None):
    raw = str(text or "")
    observed = _clean(raw)
    if len(observed) < 6:
        return None
    vocabulary = tuple(_clean(name) for name in (names if names is not None else _NAMES.get()))
    if observed in vocabulary:
        return None
    changes = []
    candidates = {}
    for target in vocabulary:
        if len(target) < 6 or abs(len(observed) - len(target)) > MAX_NAME_EDITS + 2 * MAX_EDGE_NOISE:
            continue
        for leading in range(MAX_EDGE_NOISE + 1):
            for trailing in range(MAX_EDGE_NOISE + 1):
                end = len(observed) - trailing
                span = observed[leading:end]
                if len(span) < 6 or abs(len(span) - len(target)) > MAX_NAME_EDITS:
                    continue
                # A company/station number is identity, never a fuzzy letter.
                if re.findall(r"\d+", observed) != re.findall(r"\d+", target):
                    continue
                alignment = SequenceMatcher(None, span, target)
                if alignment.ratio() < MIN_NAME_SIMILARITY:
                    continue
                if any(tag == "delete" and (i1 == 0 or i2 == len(span))
                       for tag, i1, i2, _, _ in alignment.get_opcodes()):
                    continue
                distance = _distance(span, target)
                if distance > MAX_NAME_EDITS:
                    continue
                actual_branch = _BRANCH_SUFFIX.search(span)
                target_branch = _BRANCH_SUFFIX.search(target)
                original_branch = _BRANCH_SUFFIX.search(observed)
                # A two-character place/department after the company must not
                # disappear as "edge noise" merely because its parent is also
                # registered. One trailing stray glyph remains recoverable.
                if not target_branch and original_branch and len(original_branch[1]) >= 2:
                    continue
                if bool(actual_branch) != bool(target_branch):
                    continue
                if actual_branch and _distance(actual_branch[1], target_branch[1], MAX_BRANCH_EDITS) > MAX_BRANCH_EDITS:
                    continue
                rank = (distance, leading + trailing)
                if target not in candidates or rank < candidates[target][0]:
                    candidates[target] = (rank, leading, trailing)
    if candidates:
        best_distance = min(entry[0][0] for entry in candidates.values())
        # Two equally close company names are ambiguous even if whitespace or
        # edge trimming makes one superficially neater. Never choose the one
        # printed in the current receipt's requested stamp wording.
        best = [target for target, entry in candidates.items() if entry[0][0] == best_distance]
        if len(best) == 1:
            target = best[0]
            _, leading, trailing = candidates[target]
            if target != observed:
                changes.append({"rule": "unique_registered_name", "before": observed,
                                "after": target, "character_edits": best_distance,
                                "leading_noise": observed[:leading],
                                "trailing_noise": observed[len(observed) - trailing:] if trailing else ""})
                observed = target
    if not changes:
        return None
    return {"raw_text": raw, "corrected_text": observed, "changes": changes,
            "policy": "registered_company_names", "vocabulary_size": len(vocabulary)}


def correct_local_seal_names(artifact, texts):
    """Correct active name strings only; retain OCR observations and pixels."""
    fields = ["unwrapped_text"]
    if artifact.get("shape_code") == "rectangle":
        fields.append("rectangular_row_texts")
    replacements, evidence = {}, []
    for field in fields:
        values = artifact.get(field)
        for value in values if isinstance(values, list) else [values]:
            if not value:
                continue
            correction = correct_company_name(value)
            if correction is not None:
                replacements[value] = correction["corrected_text"]
                evidence.append({"field": field, **correction})
    if not evidence:
        return texts

    def replace(value):
        if not isinstance(value, str):
            return value
        for old, new in sorted(replacements.items(), key=lambda pair: -len(pair[0])):
            value = value.replace(old, new)
        return value

    raw_fields = {}
    for field in ("unwrapped_text", "unwrap_texts", "rectangular_text", "rectangular_texts",
                  "rectangular_row_texts", "combined_text", "matching_texts"):
        value = artifact.get(field)
        corrected = [replace(item) for item in value] if isinstance(value, list) else replace(value)
        if value != corrected:
            raw_fields[field] = value
            artifact[field] = corrected
    artifact["name_corrections"] = evidence
    artifact["name_correction_raw_fields"] = raw_fields
    for read in artifact.get("reads", []):
        corrected = replace(read.get("text"))
        if corrected != read.get("text"):
            read["corrected_text"] = corrected
    return [replace(text) for text in texts]
