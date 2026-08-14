from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module


def main() -> int:
    parser = argparse.ArgumentParser(description="离线导出指定批次的 Excel 文件")
    parser.add_argument(
        "task_id",
        nargs="?",
        default="",
        help="本地数据库中的批次编号；省略时导出全部最新回单",
    )
    parser.add_argument("--output", type=Path, required=True, help="输出 .xlsx 路径")
    parser.add_argument(
        "--backend",
        default="",
        help="统计摘要限定 OCR 后端（例如 hybrid）；留空则统计全部后端",
    )
    args = parser.parse_args()

    app_module.initialize()
    query = {}
    if args.task_id:
        query["task_id"] = args.task_id
    if args.backend:
        query["ocr_backend"] = args.backend
    response = app_module.app.test_client().get("/api/export.xlsx", query_string=query)
    if response.status_code != 200:
        raise RuntimeError(
            f"Excel 导出失败（HTTP {response.status_code}）："
            f"{response.get_data(as_text=True)}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(response.data)
    print(f"exported={args.output.resolve()} bytes={len(response.data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
