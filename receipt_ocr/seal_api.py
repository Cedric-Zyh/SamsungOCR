from __future__ import annotations

import os
from pathlib import Path

import requests


DEFAULT_API_KEY_FILE = Path(__file__).resolve().parent.parent / "config" / "seal_api_key"


SEAL_RECOGNITION_MODES = (
    {
        "id": "local",
        "label": "本地印章识别",
        "description": "仅使用当前 OCR 引擎识别印章，不上传图片",
    },
    {
        "id": "qingtong",
        "label": "本地 + 清瞳印章 API",
        "description": "会将整张回单上传至清瞳接口，并与本地印章文字交叉验证",
    },
)


def resolve_seal_recognition_mode(value: str | None) -> str:
    selected = str(value or "local").strip().lower()
    valid = {item["id"] for item in SEAL_RECOGNITION_MODES}
    if selected not in valid:
        raise ValueError(f"不支持的印章识别方式：{selected}")
    return selected


class SealApiClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("SEAL_API_URL", "https://seal.qingtong.cn").rstrip("/")
        self.api_key_file = Path(
            os.getenv("SEAL_API_KEY_FILE", str(DEFAULT_API_KEY_FILE))
        ).expanduser()
        self.api_key = os.getenv("SEAL_API_KEY", "").strip() or self._read_api_key_file()
        self.timeout = float(os.getenv("SEAL_API_TIMEOUT", "120"))

    def _read_api_key_file(self) -> str:
        try:
            return self.api_key_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def recognize(self, image_path: str | Path) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "requested": True,
                "message": (
                    "未配置 SEAL_API_KEY 或密钥文件，已回退到本地印章识别"
                ),
            }
        path = Path(image_path)
        try:
            with path.open("rb") as handle:
                response = requests.post(
                    f"{self.base_url}/api/v1/recognize",
                    headers={"X-API-Key": self.api_key},
                    files={"files": (path.name, handle, _mime(path))},
                    timeout=self.timeout,
                )
        except (OSError, requests.RequestException) as exc:
            return {
                "enabled": True,
                "requested": True,
                "ok": False,
                "message": f"清瞳印章 API 调用失败，已保留本地识别：{exc}",
            }
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:500]}
        business_code = payload.get("code") if isinstance(payload, dict) else None
        ok = bool(response.ok and business_code == 200)
        result = {
            "enabled": True,
            "requested": True,
            "ok": ok,
            "http_status": response.status_code,
            "business_code": business_code,
            "response": payload,
        }
        if not ok:
            if response.ok and business_code is None:
                reason = "响应缺少业务状态码"
            elif response.ok:
                reason = f"业务状态码 {business_code}"
            else:
                reason = f"HTTP {response.status_code}"
            result["message"] = f"清瞳印章 API 未成功（{reason}），已保留本地识别"
        return result

    @staticmethod
    def skipped() -> dict:
        return {
            "enabled": False,
            "requested": False,
            "message": "本次选择仅使用本地印章识别",
        }


def _mime(path: Path) -> str:
    return {
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
