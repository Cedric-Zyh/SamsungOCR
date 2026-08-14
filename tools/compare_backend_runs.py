from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.backend_benchmark import compare_backend_runs


def main() -> None:
    parser = argparse.ArgumentParser(description="比较离线 OCR 后端批次")
    parser.add_argument(
        "--run", action="append", required=True, metavar="BACKEND=JSON",
        help="后端与 batch_validate JSON，可重复",
    )
    parser.add_argument("--reference-backend", required=True)
    parser.add_argument("--ground-truth", default="数据/ground_truth.json")
    parser.add_argument("--document-truth", default="数据/document_ground_truth.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    runs = {}
    for value in args.run:
        if "=" not in value:
            raise ValueError(f"--run 格式应为 BACKEND=JSON：{value}")
        backend, filename = value.split("=", 1)
        payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        runs.setdefault(backend, []).extend(payload.get("results") or [])
    ground_truth = json.loads(Path(args.ground_truth).read_text(encoding="utf-8"))
    document_truth = json.loads(Path(args.document_truth).read_text(encoding="utf-8"))
    report = compare_backend_runs(
        runs, ground_truth, document_truth,
        reference_backend=args.reference_backend,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
