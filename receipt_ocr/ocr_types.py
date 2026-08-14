from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class TextObservation:
    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict:
        return asdict(self)


def observations_text(rows: Iterable[TextObservation]) -> str:
    return "\n".join(row.text for row in rows if row.text)
