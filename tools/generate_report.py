from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module


def main() -> int:
    parser = argparse.ArgumentParser(description="离线生成与 /api/report 相同的准确率报表")
    parser.add_argument("--backend", default="hybrid", help="OCR 后端筛选")
    parser.add_argument("--output", type=Path, required=True, help="JSON 输出路径")
    args = parser.parse_args()

    app_module.initialize()
    response = app_module.app.test_client().get(
        "/api/report", query_string={"ocr_backend": args.backend}
    )
    if response.status_code != 200:
        raise RuntimeError(f"报表生成失败：{response.get_data(as_text=True)}")
    payload = response.get_json()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    accuracy = payload.get("accuracy") or {}
    print(
        f"samples={accuracy.get('tested_samples', 0)} "
        f"fields={accuracy.get('field_accuracy', 0):.2%} "
        f"date={accuracy.get('date_accuracy', 0):.2%} "
        f"seal={accuracy.get('seal_conclusion_accuracy', 0):.2%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
