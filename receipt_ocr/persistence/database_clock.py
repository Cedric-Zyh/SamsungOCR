"""Small clock boundary used by persistence adapters."""

from datetime import datetime


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

