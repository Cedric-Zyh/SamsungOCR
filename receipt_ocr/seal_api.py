from __future__ import annotations

import os
from pathlib import Path

import requests


class SealApiClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("SEAL_API_URL", "https://seal.qingtong.cn").rstrip("/")
        self.api_key = os.getenv("SEAL_API_KEY", "").strip()
        self.timeout = float(os.getenv("SEAL_API_TIMEOUT", "120"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def recognize(self, image_path: str | Path) -> dict:
        if not self.enabled:
            return {"enabled": False, "message": "未配置 SEAL_API_KEY，使用本地识别"}
        path = Path(image_path)
        with path.open("rb") as handle:
            response = requests.post(
                f"{self.base_url}/api/v1/recognize",
                headers={"X-API-Key": self.api_key},
                files={"files": (path.name, handle, _mime(path))},
                timeout=self.timeout,
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:500]}
        return {
            "enabled": True,
            "ok": response.ok,
            "http_status": response.status_code,
            "response": payload,
        }


def _mime(path: Path) -> str:
    return {
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
