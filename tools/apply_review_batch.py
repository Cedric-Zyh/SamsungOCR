from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module


def main() -> int:
    parser = argparse.ArgumentParser(description="通过应用复核接口批量保存人工确认与评测真值")
    parser.add_argument("spec", type=Path, help="包含 result_id 和 payload 的 JSON 文件")
    args = parser.parse_args()

    items = json.loads(args.spec.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("复核规格必须是 JSON 数组")

    app_module.initialize()
    client = app_module.app.test_client()
    for item in items:
        result_id = int(item["result_id"])
        response = client.patch(
            f"/api/results/{result_id}/review",
            json=item["payload"],
        )
        body = response.get_json()
        if response.status_code != 200:
            raise RuntimeError(f"结果 {result_id} 复核失败：{body}")
        saved = body.get("ground_truth_saved") or {}
        print(
            f"{body['filename']}: {body['review_status']} / {body['final_result']} "
            f"truth={saved.get('action', '未保存')} total={saved.get('total', '-') }"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
