"""Read-only statistics derived from persisted receipt records."""

from __future__ import annotations

import json


class StatisticsMixin:
    def import_date_counts(self, month: str, ocr_backend: str = "") -> dict[str, int]:
        """Count distinct imported filenames per day before other filters."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,filename,created_at,result_json FROM results "
                "WHERE deleted_at='' AND substr(created_at,1,7)=?",
                (month,),
            ).fetchall()
        days: dict[str, set[str]] = {}
        for row in rows:
            if ocr_backend and json.loads(row["result_json"]).get("ocr_backend", "") != ocr_backend:
                continue
            day = row["created_at"][:10]
            days.setdefault(day, set()).add(row["filename"] or f"__result_{row['id']}")
        return {day: len(names) for day, names in days.items()}
