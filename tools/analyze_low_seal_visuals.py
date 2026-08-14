from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.image_processing import _color_masks


ARTIFACT_MARKER = "/files/artifacts/"
STAMP_TYPE_TOKENS = (
    "专用章",
    "收货章",
    "业务章",
    "服务中心",
    "维修中心",
    "服务总汇",
)


def resolve_artifact_url(url: str, artifact_root: Path) -> Path | None:
    if ARTIFACT_MARKER not in url:
        return None
    relative = url.split(ARTIFACT_MARKER, 1)[1]
    path = artifact_root / relative
    return path if path.is_file() else None


def color_pixel_ratio(path: Path) -> tuple[float, int, int]:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return 0.0, 0, 0
    red, blue = _color_masks(image)
    mask = cv2.bitwise_or(red, blue)
    height, width = image.shape[:2]
    ratio = float(cv2.countNonZero(mask)) / float(max(1, mask.size))
    return ratio, width, height


def color_band(ratio: float) -> str:
    if ratio < 0.03:
        return "章色极弱"
    if ratio < 0.10:
        return "章色偏弱"
    return "章色充足"


def evidence_subcategory(row: dict, ratio: float) -> str:
    band = color_band(ratio)
    if band != "章色充足":
        return band
    requirement = str(row.get("requirement", ""))
    recognized = str(row.get("recognized", ""))
    shared_type = next(
        (
            token
            for token in STAMP_TYPE_TOKENS
            if token in requirement and token in recognized
        ),
        "",
    )
    if shared_type and float(row.get("company_score", 0) or 0) < 0.50:
        return "章型已识别但主体缺失"
    expected_digits = "".join(char for char in requirement if char.isdigit())
    observed_digits = "".join(char for char in recognized if char.isdigit())
    if expected_digits and observed_digits:
        return "编号或站点结构残缺"
    if float(row.get("company_score", 0) or 0) >= 0.40:
        return "公司主体残缺"
    if len(row.get("shapes") or []) > 1:
        return "多章或混合形状文字碎片化"
    return "章色充足但文字碎片化"


def analyze_report(report: dict, artifact_root: Path) -> dict:
    rows: list[dict] = []
    color_bands: Counter[str] = Counter()
    subcategories: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    missing_images = 0
    candidates = [
        row
        for row in report.get("samples", [])
        if row.get("category") == "低相似度证据"
    ]
    for row in candidates:
        image_metrics = []
        for url in row.get("artifact_urls") or []:
            if not str(url).endswith("-original.jpg"):
                continue
            path = resolve_artifact_url(str(url), artifact_root)
            if path is None:
                continue
            ratio, width, height = color_pixel_ratio(path)
            image_metrics.append({
                "url": url,
                "path": str(path.resolve()),
                "color_pixel_ratio": round(ratio, 4),
                "width": width,
                "height": height,
            })
        if not image_metrics:
            missing_images += 1
            ratio = 0.0
            strongest = None
        else:
            strongest = max(
                image_metrics,
                key=lambda item: float(item["color_pixel_ratio"]),
            )
            ratio = float(strongest["color_pixel_ratio"])
        band = color_band(ratio)
        subcategory = evidence_subcategory(row, ratio)
        color_bands[band] += 1
        subcategories[subcategory] += 1
        for shape in row.get("shapes") or ["未知"]:
            shapes[str(shape)] += 1
        server_priority = bool(
            not row.get("server_audited")
            and ratio >= 0.10
            and (
                float(row.get("company_score", 0) or 0) >= 0.25
                or subcategory
                in {
                    "章型已识别但主体缺失",
                    "编号或站点结构残缺",
                    "公司主体残缺",
                }
            )
        )
        rows.append({
            "filename": row.get("filename"),
            "result_id": row.get("result_id"),
            "truth_should_match": row.get("truth_should_match"),
            "requirement": row.get("requirement"),
            "recognized": row.get("recognized"),
            "score": row.get("score"),
            "company_score": row.get("company_score"),
            "shapes": row.get("shapes") or [],
            "server_audited": bool(row.get("server_audited")),
            "color_band": band,
            "evidence_subcategory": subcategory,
            "server_priority": server_priority,
            "strongest_original": strongest,
            "original_images": image_metrics,
        })
    rows.sort(key=lambda row: (
        not row["server_priority"],
        -float((row["strongest_original"] or {}).get("color_pixel_ratio", 0)),
        -float(row.get("score") or 0),
        str(row.get("filename") or ""),
    ))
    return {
        "source_backend": report.get("backend"),
        "low_similarity_samples": len(candidates),
        "samples_with_images": len(candidates) - missing_images,
        "missing_original_images": missing_images,
        "color_bands": dict(color_bands),
        "evidence_subcategories": dict(subcategories),
        "shape_counts": dict(shapes),
        "server_priority_samples": sum(row["server_priority"] for row in rows),
        "samples": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按章色、形状和文字结构分析低相似度印章"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("storage/seal-gap-analysis-301-hybrid.json"),
    )
    parser.add_argument(
        "--artifact-root", type=Path, default=Path("storage/artifacts")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = analyze_report(report, args.artifact_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "low_similarity_samples": result["low_similarity_samples"],
        "color_bands": result["color_bands"],
        "evidence_subcategories": result["evidence_subcategories"],
        "server_priority_samples": result["server_priority_samples"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
