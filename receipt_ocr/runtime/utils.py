"""Small evidence utilities shared across recognition stages."""

from __future__ import annotations


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = "".join(cleaned.split())
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output
