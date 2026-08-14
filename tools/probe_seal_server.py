from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import combine_region_texts
from receipt_ocr.database import Database
from receipt_ocr.image_processing import (
    save_rectangular_seal_bands,
    save_unwrapped_seal_bands,
)
from receipt_ocr.paddle_ocr import recognize_line, recognize_text
from receipt_ocr.parser import compare_seal_text


ARTIFACT_MARKER = "/files/artifacts/"
SAFE_VARIANTS = (
    ("保留章色白底图", "color_isolated_url", False),
    ("圆章/矩形校正图", "unwrapped_url", False),
    ("圆章展开 180°", "unwrapped_rotated_url", False),
    ("保留章色旋转对照图", "color_isolated_rotations_url", False),
    ("矩形编号章数字行", "code_line_url", True),
)


def resolve_artifact_url(url: str, artifact_root: Path) -> Path | None:
    if ARTIFACT_MARKER not in str(url or ""):
        return None
    path = artifact_root / str(url).split(ARTIFACT_MARKER, 1)[1]
    return path if path.is_file() else None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def save_unwrapped_bands(
    source: Path,
    destination_dir: Path,
    *,
    filename: str,
    region_index: int,
) -> list[Path]:
    """Split the three auditable polar strips without changing their pixels."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem or "receipt"
    return save_unwrapped_seal_bands(
        source,
        destination_dir / f"{stem}-seal-{region_index}-unwrapped-band",
    )


def save_rectangular_bands(
    source: Path,
    destination_dir: Path,
    *,
    filename: str,
    region_index: int,
) -> list[Path]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem or "receipt"
    return save_rectangular_seal_bands(
        source,
        destination_dir / f"{stem}-seal-{region_index}-rectangular-band",
    )


def probe_record(
    record: dict,
    artifact_root: Path,
    derived_root: Path | None = None,
    *,
    models: tuple[str, ...] = ("mobile", "server"),
    bands_only: bool = False,
) -> dict:
    seal = record.get("seal") or record.get("seal_check") or {}
    requirement = str(seal.get("requirement") or "")
    existing = str(seal.get("recognized") or "")
    artifacts = (record.get("artifacts") or record.get("processing_artifacts") or {}).get(
        "seals", []
    )
    regions = []
    all_server_texts: list[str] = []
    all_mobile_texts: list[str] = []
    for region_index, artifact in enumerate(artifacts):
        variants = []
        region_server: list[str] = []
        region_mobile: list[str] = []
        existing_region_texts = _dedupe([
            str(artifact.get("combined_text") or ""),
            str(artifact.get("same_region_reconstructed_text") or ""),
        ])
        for label, key, line_only in SAFE_VARIANTS:
            if bands_only and key != "unwrapped_url":
                continue
            path = resolve_artifact_url(str(artifact.get(key) or ""), artifact_root)
            if path is None:
                continue
            if not bands_only:
                model_rows = {}
                for model in models:
                    try:
                        rows = (
                            recognize_line(path, model_variant=model)
                            if line_only
                            else recognize_text(
                                path, model_variant=model, min_text_height=0.012
                            )
                        )
                    except Exception as exc:  # Keep a failed variant auditable.
                        model_rows[model] = {"texts": [], "error": str(exc)}
                        continue
                    texts = _dedupe([row.text for row in rows])
                    model_rows[model] = {"texts": texts, "error": ""}
                    if model == "server":
                        region_server.extend(texts)
                    else:
                        region_mobile.extend(texts)
                variants.append({
                    "preprocessing": label,
                    "path": str(path.resolve()),
                    "models": model_rows,
                })
            if key == "unwrapped_url" and derived_root is not None:
                rectangular = str(artifact.get("shape") or "") == "矩形"
                band_paths = (
                    save_rectangular_bands(
                        path,
                        derived_root,
                        filename=str(record.get("filename") or "receipt"),
                        region_index=region_index,
                    )
                    if rectangular else
                    save_unwrapped_bands(
                        path,
                        derived_root,
                        filename=str(record.get("filename") or "receipt"),
                        region_index=region_index,
                    )
                )
                for band_index, band_path in enumerate(band_paths, start=1):
                    band_models = {}
                    for model in models:
                        try:
                            detection_rows = recognize_text(
                                band_path,
                                model_variant=model,
                                min_text_height=0.012,
                            )
                            texts = _dedupe([row.text for row in detection_rows])
                            band_models[model] = {"texts": texts, "error": ""}
                            if model == "server":
                                region_server.extend(texts)
                            else:
                                region_mobile.extend(texts)
                        except Exception as exc:
                            band_models[model] = {"texts": [], "error": str(exc)}
                    variants.append({
                        "preprocessing": (
                            f"矩形章横向分带 {band_index}"
                            if rectangular else
                            f"圆章展开分带 {band_index}"
                        ),
                        "path": str(band_path.resolve()),
                        "models": band_models,
                    })
        region_server = _dedupe(region_server)
        region_mobile = _dedupe(region_mobile)
        all_server_texts.extend(region_server)
        all_mobile_texts.extend(region_mobile)
        server_aggregate = combine_region_texts(region_server)
        mobile_aggregate = combine_region_texts(region_mobile)
        regions.append({
            "region_index": region_index,
            "variants": variants,
            "mobile_texts": region_mobile,
            "server_texts": region_server,
            "mobile_aggregate": mobile_aggregate,
            "server_aggregate": server_aggregate,
            "mobile_match": compare_seal_text(
                requirement, region_mobile + ([mobile_aggregate] if mobile_aggregate else [])
            ),
            "existing_plus_mobile_match": compare_seal_text(
                requirement,
                _dedupe(existing_region_texts + region_mobile),
            ),
            "server_match": compare_seal_text(
                requirement, region_server + ([server_aggregate] if server_aggregate else [])
            ),
        })
    all_server_texts = _dedupe(all_server_texts)
    all_mobile_texts = _dedupe(all_mobile_texts)
    return {
        "filename": record.get("filename"),
        "requirement": requirement,
        "existing_recognized": existing,
        "existing_match": compare_seal_text(requirement, [existing]),
        "mobile_probe_match": compare_seal_text(requirement, all_mobile_texts),
        "server_probe_match": compare_seal_text(requirement, all_server_texts),
        "existing_plus_mobile_match": compare_seal_text(
            requirement, _dedupe([existing] + all_mobile_texts)
        ),
        "existing_plus_server_match": compare_seal_text(
            requirement, _dedupe([existing] + all_server_texts)
        ),
        "regions": regions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在安全章色衍生图上离线比较 Paddle Mobile/Server，不修改生产结果"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("storage/artifacts"))
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument(
        "--with-unwrapped-bands",
        action="store_true",
        help="额外保存并探测圆章展开图中的三条独立分带",
    )
    parser.add_argument(
        "--bands-only",
        action="store_true",
        help="只运行新增圆章分带，不重复既有安全变换",
    )
    parser.add_argument(
        "--models",
        default="mobile,server",
        help="逗号分隔的 mobile/server 模型列表",
    )
    parser.add_argument("--sample", action="append", default=[])
    args = parser.parse_args()

    wanted = set(args.sample)
    if args.input:
        report = json.loads(args.input.read_text(encoding="utf-8"))
        records = report.get("records", [])
        input_label = str(args.input.resolve())
    else:
        records = Database(args.database).list_results(
            limit=5000,
            filters={"ocr_backend": args.backend},
            latest_by_filename=True,
        )
        input_label = str(args.database.resolve())
    records = [
        record for record in records
        if not wanted or record.get("filename") in wanted
    ]
    derived_root = None
    if args.with_unwrapped_bands:
        derived_root = args.output.parent / f"{args.output.stem}-bands"
    models = tuple(
        model.strip() for model in args.models.split(",")
        if model.strip() in {"mobile", "server"}
    )
    if not models:
        parser.error("--models 至少包含 mobile 或 server")
    output = {
        "input": input_label,
        "backend": args.backend if args.database else "",
        "sample_count": len(records),
        "samples": [
            probe_record(
                record,
                args.artifact_root,
                derived_root,
                models=models,
                bands_only=args.bands_only,
            )
            for record in records
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "sample_count": len(records),
        "server_reliable": sum(
            bool(row["server_probe_match"].get("reliable")) for row in output["samples"]
        ),
        "existing_plus_server_reliable": sum(
            bool(row["existing_plus_server_match"].get("reliable"))
            for row in output["samples"]
        ),
        "existing_plus_mobile_reliable": sum(
            bool(row["existing_plus_mobile_match"].get("reliable"))
            for row in output["samples"]
        ),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
