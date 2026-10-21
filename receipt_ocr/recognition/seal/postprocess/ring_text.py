"""Recover the reading order of text cut by a circular unwrap seam."""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from receipt_ocr.domain.parsing.parsing_seals import normalize_seal_text_strict


_TEXT_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")
_COMPANY_SUFFIXES = (
    "有限责任公司",
    "有限公司",
    "分公司",
    "公司",
)


def _clean(text: str) -> str:
    return _TEXT_RE.sub("", str(text or "")).strip()


def _requirement_company(requirement: str) -> str:
    text = _clean(requirement)
    matches = []
    for suffix in _COMPANY_SUFFIXES:
        matches.extend(re.findall(rf"[0-9A-Za-z\u4e00-\u9fff]{{3,}}{re.escape(suffix)}", text))
    return max(matches, key=len, default="")


def _rotations(text: str) -> list[tuple[int, str]]:
    return [(offset, text[offset:] + text[:offset]) for offset in range(len(text))]


def _suffix_score(text: str) -> float:
    # A branch belongs after its parent company. Prefer the complete suffix
    # over rotating “第二分公司” (or “天津分公司”) ahead of the parent name.
    # This only ranks existing cyclic readings; missing glyphs stay missing.
    if re.search(r"(?:有限责任公司|有限公司)[\u4e00-\u9fff0-9]{0,20}分公司$", text):
        return 1.08
    if text.endswith("有限责任公司"):
        return 1.0
    if text.endswith("有限公司"):
        return 0.98
    if text.endswith("分公司"):
        return 0.94
    if text.endswith("公司"):
        return 0.88
    return 0.0


def _candidate_score(text: str, requirement_company: str) -> tuple[float, float, float]:
    suffix = _suffix_score(text)
    starts_with_suffix = any(text.startswith(value) for value in _COMPANY_SUFFIXES)
    structure = suffix - (0.35 if starts_with_suffix else 0.0)
    similarity = (
        SequenceMatcher(None, text, requirement_company).ratio()
        if requirement_company else 0.0
    )
    if requirement_company and (text in requirement_company or requirement_company in text):
        similarity = min(1.0, similarity + 0.25)
    return structure, similarity, len(text)


def reorder_ring_text(raw_text: str, requirement: str = "", alternatives: list[str] | None = None) -> dict:
    """Align a complete ring reading, then fall back to company-suffix ordering.

    Preserve the original order in ``raw_text`` after the same typography
    normalization used by strict seal comparison.  An exact requirement match
    must preserve every character and its count, and only move the circular
    seam.  Never insert a missing glyph or arbitrarily permute characters.
    Company-suffix structure remains a fallback for incomplete requirements.
    """
    raw = normalize_seal_text_strict(str(raw_text or ""))
    sources = [raw, *(normalize_seal_text_strict(str(value or "")) for value in alternatives or [])]
    sources = list(dict.fromkeys(value for value in sources if value))
    requirement_company = _requirement_company(requirement)
    if not raw:
        return {
            "raw_text": "",
            "text": "",
            "changed": False,
            "seam_offset": 0,
            "source": "",
            "requirement_company": requirement_company,
            "score": 0.0,
            "reason": "empty",
        }

    expected = normalize_seal_text_strict(str(requirement or ""))
    if expected:
        expected_counts = Counter(expected)
        for source in sources:
            if len(source) != len(expected) or Counter(source) != expected_counts:
                continue
            offset = (source + source).find(expected)
            if not 0 <= offset < len(source):
                continue
            selected = source[offset:] + source[:offset]
            return {
                "raw_text": raw,
                "text": selected,
                "changed": selected != raw,
                "seam_offset": offset,
                "source": source,
                "requirement_company": requirement_company,
                "score": 1.0,
                "raw_score": round(SequenceMatcher(None, raw, expected).ratio(), 4),
                "reason": "exact_requirement_cyclic_reorder" if selected != raw else "kept_raw_order",
            }

    candidates = []
    for source_index, source in enumerate(sources):
        for offset, text in _rotations(source):
            structure, similarity, length = _candidate_score(text, requirement_company)
            # Prefer the original start when scores tie, so this remains a
            # conservative normalization rather than an arbitrary rotation.
            candidates.append({
                "text": text,
                "source": source,
                "source_index": source_index,
                "seam_offset": offset,
                "structure": structure,
                "requirement_similarity": similarity,
                "length": length,
            })
    best = max(
        candidates,
        key=lambda item: (
            round(item["structure"], 4),
            round(item["requirement_similarity"], 4),
            item["length"],
            -item["source_index"],
            -item["seam_offset"],
        ),
    )
    raw_structure, raw_similarity, _ = _candidate_score(raw, requirement_company)
    should_reorder = (
        best["text"] != raw
        and best["structure"] > raw_structure
        and (best["structure"] - raw_structure >= 0.12 or best["requirement_similarity"] - raw_similarity >= 0.12)
    )
    selected = best["text"] if should_reorder else raw
    return {
        "raw_text": raw,
        "text": selected,
        "changed": selected != raw,
        "seam_offset": best["seam_offset"] if should_reorder else 0,
        "source": best["source"] if should_reorder else raw,
        "requirement_company": requirement_company,
        "score": round(best["requirement_similarity"], 4),
        "raw_score": round(raw_similarity, 4),
        "reason": "company_suffix_cyclic_reorder" if should_reorder else "kept_raw_order",
    }


__all__ = ["reorder_ring_text"]
