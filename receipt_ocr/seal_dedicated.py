"""Stamp-only Paddle SealOCR: three inputs, independently auditable readings."""

from contextlib import nullcontext
from pathlib import Path
import tempfile

from .image_processing import (
    save_region_crop, save_isolated_seal, save_color_isolated_seal,
    seal_region_is_rectangular,
)
from .paddle_ocr import recognize_seal_text
from .ocr_backends import backend_label


def recognize_regions(
    source, regions, artifact_dir, artifact_url_prefix, *, orientation_mode="polygon"
):
    candidates, artifacts = [], []
    context = (nullcontext(str(Path(artifact_dir) / "seals")) if artifact_dir
               else tempfile.TemporaryDirectory(prefix="receipt-seal-specialized-"))
    with context as directory:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for index, region in enumerate(regions):
            artifact = {
                "index": index, "color": region.color, "role": region.role,
                "shape": "矩形" if seal_region_is_rectangular(source, region) else "圆形",
                "ocr_backend": backend_label("paddle_seal"),
                "seal_model_backend": "PP-OCRv4_mobile_seal_det + PP-OCRv6_small_rec",
                "seal_model_readings": [],
            }
            prepared_paths = {}
            for key, title, filename, prepare in (
                ("original", "印章原始区域", f"seal-{index}-original.jpg", save_region_crop),
                ("isolated", "颜色分离高对比图", f"seal-{index}-isolated.png", save_isolated_seal),
                ("color_isolated", "保留章色白底图", f"seal-{index}-color-isolated.png", save_color_isolated_seal),
            ):
                path = directory / filename
                prepare(source, path, region)
                prepared_paths[key] = path
                url = f"{artifact_url_prefix.rstrip('/')}/seals/{filename}" if artifact_dir else ""
                artifact[f"{key}_url"] = url
                reading = {"variant": key, "label": title, "url": url,
                           "texts": [], "observations": [], "error": "",
                           "used_for_matching": key != "original"}
                try:
                    rows = recognize_seal_text(path)
                    reading["texts"] = [row.text for row in rows if row.text]
                    reading["observations"] = [row.to_dict() for row in rows]
                except Exception as exc:
                    reading["error"] = str(exc)
                # Preserve raw reading order and never complete text from the
                # requirement. Raw crops can include black form/requirement
                # text, so display them but only compare colour-safe variants.
                reading["text"] = "".join(reading["texts"])
                if reading["used_for_matching"] and reading["text"]:
                    candidates.append(reading["text"])
                artifact["seal_model_readings"].append(reading)
            # The dedicated SealOCR route is excellent at curved company
            # lettering, but its result can still lose the first character of
            # a diagonal ``收货专用章`` line.  Use a regular Paddle polygon
            # only to estimate that line's direction, then re-read the
            # color-safe crop after rotation.  The original three readings
            # remain untouched and auditable.
            artifact["orientation"] = {}
            oriented = None
            if ((artifact["shape"] == "圆形" or orientation_mode == "doc_ori")
                    and prepared_paths.get("color_isolated")):
                try:
                    from .seal_orientation import (
                        prepare_round_stamp,
                        prepare_round_stamp_doc_ori,
                    )

                    oriented_path = directory / f"seal-{index}-color-isolated-oriented.png"
                    if orientation_mode == "doc_ori":
                        oriented, decision = prepare_round_stamp_doc_ori(
                            prepared_paths["color_isolated"], oriented_path
                        )
                    elif orientation_mode == "polygon":
                        oriented, decision = prepare_round_stamp(
                            prepared_paths["color_isolated"],
                            oriented_path,
                            model_variant="v6",
                        )
                    else:
                        oriented, decision = None, {
                            "mode": "none",
                            "status": "按设置保留印章原方向",
                            "angle": None,
                            "applied_rotation": 0.0,
                            "anchor_text": "",
                            "confidence": 1.0,
                        }
                    artifact["orientation"] = decision
                except Exception as exc:
                    artifact["orientation"] = {
                        "status": "印章方向检测失败，保留原方向",
                        "error": str(exc),
                        "applied_rotation": 0.0,
                    }
            artifact["color_isolated_oriented_url"] = (
                f"{artifact_url_prefix.rstrip('/')}/seals/{oriented.name}"
                if oriented is not None and oriented.is_file() else ""
            )
            if oriented is not None and oriented.is_file():
                reading = {
                    "variant": "color_isolated_oriented",
                    "label": "按印章文字方向旋正后的保留章色白底图",
                    "url": artifact["color_isolated_oriented_url"],
                    "texts": [],
                    "observations": [],
                    "error": "",
                    "used_for_matching": True,
                }
                try:
                    rows = recognize_seal_text(oriented)
                    reading["texts"] = [row.text for row in rows if row.text]
                    reading["observations"] = [row.to_dict() for row in rows]
                except Exception as exc:
                    reading["error"] = str(exc)
                reading["text"] = "".join(reading["texts"])
                if reading["text"]:
                    candidates.append(reading["text"])
                artifact["seal_model_readings"].append(reading)
            artifacts.append(artifact)
    return list(dict.fromkeys(candidates)), artifacts
