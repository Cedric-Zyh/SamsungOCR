from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from receipt_ocr import ocr_backends
from receipt_ocr.paddle_ocr import paddle_model_home


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "未安装"


def _collect(*, write_check: bool) -> tuple[dict, list[str]]:
    catalog = ocr_backends.backend_catalog()
    indexed = {item["id"]: item for item in catalog}
    failures: list[str] = []
    try:
        default = ocr_backends.default_backend()
    except Exception as exc:
        default = ""
        failures.append(f"无法选择默认后端：{exc}")
    try:
        route = ocr_backends.backend_route("hybrid")
    except Exception as exc:
        route = {}
        failures.append(f"Hybrid 路由失败：{exc}")

    if platform.system() == "Windows":
        if indexed.get("vision", {}).get("available"):
            failures.append("Windows 不应暴露 macOS Vision")
        if default != "paddle":
            failures.append(f"Windows 默认后端应为 paddle，实际为 {default or '空'}")
        if set(route.values()) != {"paddle"}:
            failures.append(f"Windows Hybrid 应全部回退 Paddle Mobile，实际为 {route}")
        if not indexed.get("hybrid_server", {}).get("safety_policy"):
            failures.append("Windows 混合 OCR 未声明单模型人工复核安全策略")

    model_home = paddle_model_home()
    writable = None
    write_error = ""
    if write_check:
        try:
            model_home.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="samsung-ocr-", dir=model_home):
                pass
            writable = True
        except Exception as exc:
            writable = False
            write_error = str(exc)
            failures.append(f"模型目录不可写：{model_home}（{exc}）")

    report = {
        "platform": platform.system(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "model_home": str(model_home),
        "model_home_writable": writable,
        "model_home_write_error": write_error,
        "default_backend": default,
        "hybrid_route": route,
        "packages": {
            "paddlepaddle": _package_version("paddlepaddle"),
            "paddleocr": _package_version("paddleocr"),
            "opencv-contrib-python": _package_version("opencv-contrib-python"),
            "Pillow": _package_version("Pillow"),
        },
        "backends": catalog,
        "failures": failures,
        "ok": not failures,
    }
    return report, failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="三星回单 Windows PaddleOCR 路由/依赖/模型目录自检"
    )
    parser.add_argument(
        "--simulate-windows", action="store_true",
        help="在非 Windows 机器上只模拟 Windows 后端路由，不执行模型目录写入",
    )
    parser.add_argument(
        "--no-write-check", action="store_true",
        help="不测试模型缓存目录是否可写",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="可选：将完整自检结果保存为 JSON，便于跨平台验收留档",
    )
    args = parser.parse_args()

    if args.simulate_windows:
        real_find_spec = importlib.util.find_spec

        def simulated_spec(name: str):
            if name == "Vision":
                return None
            if name in {"paddle", "paddleocr"}:
                return object()
            return real_find_spec(name)

        with (
            patch.object(platform, "system", lambda: "Windows"),
            patch.object(importlib.util, "find_spec", simulated_spec),
            patch.dict(os.environ, {"OCR_BACKEND": ""}, clear=False),
        ):
            report, failures = _collect(write_check=False)
            report["platform"] = "Windows（模拟路由）"
            report["model_home"] = (
                "模拟模式不检查；Windows 使用 PADDLE_MODEL_HOME，"
                "未配置时为 %USERPROFILE%\\.paddlex"
            )
            for backend in report["backends"]:
                if backend.get("id") != "vision":
                    backend["model_home"] = report["model_home"]
    else:
        if platform.system() != "Windows":
            print("本命令应在 Windows 运行；非 Windows 请加 --simulate-windows", file=sys.stderr)
            return 2
        report, failures = _collect(write_check=not args.no_write_check)

    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
