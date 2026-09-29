"""Recover the reading order of text cut by a circular unwrap seam."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


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
    """Choose a cyclic rotation that keeps a company suffix intact.

    The OCR result remains untouched in ``raw_text``.  This function only
    chooses among observed characters; it never inserts a missing glyph from
    the requirement.  The requirement supplies a soft ranking signal, while
    company-suffix structure handles the common ``公司……有限`` seam case.
    """
    raw = _clean(raw_text)
    sources = [raw, *(_clean(value) for value in alternatives or [])]
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
