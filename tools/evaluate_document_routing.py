from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.database import Database
from receipt_ocr.document_evaluation import evaluate_document_routing


def main() -> None:
    parser = argparse.ArgumentParser(description="评估委托书、商品续页和分页回单路由")
    parser.add_argument("--task-id", action="append", required=True, help="待评估批次，可重复")
    parser.add_argument("--database", default="storage/results.db")
    parser.add_argument("--truth", default="数据/document_ground_truth.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    database = Database(args.database)
    results = []
    for task_id in args.task_id:
        results.extend(database.list_results(limit=5000, filters={"task_id": task_id}))
    report = evaluate_document_routing(truth, results)
    report["task_ids"] = args.task_id
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
