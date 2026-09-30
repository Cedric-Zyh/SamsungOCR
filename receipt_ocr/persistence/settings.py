"""Application setting persistence shared by the database facade."""

from __future__ import annotations

import json

from .constants import DEFAULT_RETENTION_DAYS, MAX_RETENTION_DAYS, MIN_RETENTION_DAYS
from .database_clock import now_iso


class SettingsMixin:
    @staticmethod
    def normalize_retention_days(value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("保留天数必须是整数")
        try:
            days = int(value)
        except (TypeError, ValueError):
            raise ValueError("保留天数必须是整数") from None
        if str(value).strip() != str(days):
            raise ValueError("保留天数必须是整数")
        if not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
            raise ValueError(
                f"保留天数必须在 {MIN_RETENTION_DAYS} 至 {MAX_RETENTION_DAYS} 天之间"
            )
        return days

    def get_retention_days(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key='retention_days'"
            ).fetchone()
        try:
            return self.normalize_retention_days(
                row["value"] if row else DEFAULT_RETENTION_DAYS
            )
        except ValueError:
            return DEFAULT_RETENTION_DAYS

    def set_retention_days(self, value: object) -> int:
        days = self.normalize_retention_days(value)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO app_settings(key,value,updated_at) VALUES('retention_days',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (str(days), now_iso()),
            )
        return days

    def get_json_setting(self, key: str, default: object = None) -> object:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def set_json_setting(self, key: str, value: object) -> object:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (key, encoded, now_iso()),
            )
        return value

