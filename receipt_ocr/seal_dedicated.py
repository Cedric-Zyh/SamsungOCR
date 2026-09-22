"""Stamp-only Paddle SealOCR: three inputs, independently auditable readings."""

from contextlib import nullcontext
from pathlib import Path
import tempfile

from .image_processing import (
    save_region_crop, save_isolated_seal, save_color_isolated_seal,
    save_ellipse_normalized_seal, save_round_seal_type_band,
    save_unwrapped_seal, save_unwrapped_seal_bands,
    seal_region_shape,
)
from .paddle_ocr import recognize_line, recognize_seal_text
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
            shape = seal_region_shape(source, region)
            artifact = {
                "index": index, "color": region.color, "role": region.role,
                "shape": {
                    "rectangle": "矩形",
                    "ellipse": "椭圆",
                    "circle": "圆形",
                }[shape],
                "qingtong_shape": region.qingtong_cls or "",
                "shape_source": "本地章色轮廓校验（清瞳章型仅作提示）",
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
                           "used_for_matching": (
                               key == "color_isolated" and artifact["shape"] != "椭圆"
                           )}
                # The raw crop is retained for visual audit, but it is not a
                # colour-safe OCR input: black table text and the printed
                # requirement can be detected together with the red seal.
                # Avoid paying for a dedicated SealOCR pass that is excluded
                # from matching anyway.  The colour-preserving derivative
                # below remains the independent raw reading; the isolated
                # high-contrast view is audit-only too.
                if key == "color_isolated":
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
            if ((artifact["shape"] in {"圆形", "椭圆"} or orientation_mode == "doc_ori")
                    and prepared_paths.get("color_isolated")):
                try:
                    from .seal_orientation import (
                        prepare_ellipse_stamp,
                        prepare_round_stamp,
                        prepare_round_stamp_doc_ori,
                    )

                    oriented_path = directory / f"seal-{index}-color-isolated-oriented.png"
                    if orientation_mode == "doc_ori":
                        oriented, decision = prepare_round_stamp_doc_ori(
                            prepared_paths["color_isolated"], oriented_path
                        )
                    elif orientation_mode == "polygon":
                        orient = (
                            prepare_ellipse_stamp
                            if artifact["shape"] == "椭圆"
                            else prepare_round_stamp
                        )
                        oriented, decision = orient(
                            prepared_paths["color_isolated"], oriented_path,
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
                    "used_for_matching": artifact["shape"] != "椭圆",
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
            if artifact["shape"] == "椭圆":
                # Ellipse-specific route: stretch first, then read the
                # normalized middle line as one logical Paddle OCR row.  The
                # raw oval reading stays visible for audit but does not enter
                # matching, so it cannot be mixed with the circle route.
                normalized_path = directory / f"seal-{index}-ellipse-normalized.png"
                save_ellipse_normalized_seal(
                    oriented or prepared_paths["color_isolated"],
                    normalized_path,
                    color=region.color,
                )
                # ``oriented`` is already the post-fine-rotation result from
                # the single orientation pass above. Keep the normalized
                # derivative and every later OCR input on that same path.
                artifact["orientation"]["ellipse_preprocess_source"] = str(
                    oriented or prepared_paths["color_isolated"]
                )
                artifact["orientation"]["ellipse_ocr_source"] = str(normalized_path)
                normalized_url = (
                    f"{artifact_url_prefix.rstrip('/')}/seals/{normalized_path.name}"
                    if artifact_dir else ""
                )
                artifact["ellipse_normalized_url"] = normalized_url
                normalized_reading = {
                    "variant": "ellipse_normalized",
                    "label": "椭圆拉伸校正图（后续 OCR 输入）",
                    "url": normalized_url,
                    "texts": [],
                    "observations": [],
                    "error": "",
                    "used_for_matching": False,
                }
                try:
                    rows = recognize_seal_text(normalized_path)
                    normalized_reading["texts"] = [row.text for row in rows if row.text]
                    normalized_reading["observations"] = [row.to_dict() for row in rows]
                except Exception as exc:
                    normalized_reading["error"] = str(exc)
                normalized_reading["text"] = "".join(normalized_reading["texts"])
                artifact["seal_model_readings"].append(normalized_reading)

                type_band_path = directory / f"seal-{index}-ellipse-type-band.png"
                save_round_seal_type_band(
                    normalized_path,
                    type_band_path,
                    orientation_aligned=True,
                )
                type_band_url = (
                    f"{artifact_url_prefix.rstrip('/')}/seals/{type_band_path.name}"
                    if artifact_dir else ""
                )
                artifact["ellipse_type_band_url"] = type_band_url
                type_reading = {
                    "variant": "ellipse_type_band",
                    "label": "椭圆章章型横向分带（拉伸后）",
                    "url": type_band_url,
                    "texts": [],
                    "observations": [],
                    "error": "",
                    "used_for_matching": True,
                }
                try:
                    rows = recognize_line(type_band_path, model_variant="v6")
                    type_reading["texts"] = [row.text for row in rows if row.text]
                    type_reading["observations"] = [row.to_dict() for row in rows]
                except Exception as exc:
                    type_reading["error"] = str(exc)
                type_reading["text"] = "".join(type_reading["texts"])
                if type_reading["text"]:
                    candidates.append(type_reading["text"])
                artifact["seal_model_readings"].append(type_reading)

                # The centre row alone explains ``收货章`` but not the
                # company around the oval.  Read the stretched ring as three
                # independent single-line inputs and merge those observations
                # into the same ellipse-only matching channel.
                unwrapped_path = directory / f"seal-{index}-ellipse-unwrapped.png"
                save_unwrapped_seal(
                    normalized_path,
                    unwrapped_path,
                    None,
                    elliptical=True,
                    normalized=True,
                    color=region.color,
                )
                unwrapped_url = (
                    f"{artifact_url_prefix.rstrip('/')}/seals/{unwrapped_path.name}"
                    if artifact_dir else ""
                )
                artifact["ellipse_unwrapped_url"] = unwrapped_url
                ring_texts = []
                ring_observations = []
                ring_band_paths = save_unwrapped_seal_bands(
                    unwrapped_path,
                    directory / f"seal-{index}-ellipse-unwrapped-band",
                )
                for band_path in ring_band_paths:
                    try:
                        rows = recognize_line(band_path, model_variant="v6")
                        ring_texts.extend(row.text for row in rows if row.text)
                        ring_observations.extend(row.to_dict() for row in rows)
                    except Exception:
                        continue
                ring_reading = {
                    "variant": "ellipse_unwrapped",
                    "label": "椭圆环形文字展开图（拉伸后）",
                    "url": unwrapped_url,
                    "texts": list(dict.fromkeys(ring_texts)),
                    "observations": ring_observations,
                    "error": "",
                    "used_for_matching": True,
                }
                ring_reading["text"] = " | ".join(ring_reading["texts"])
                # Keep each single-line observation as an independent
                # candidate.  Joining lines with ``|`` before comparison
                # makes a correct company line look like one malformed OCR
                # string and causes the matcher to fall back to ``收货章``.
                candidates.extend(ring_reading["texts"])
                artifact["seal_model_readings"].append(ring_reading)
            artifacts.append(artifact)
    return list(dict.fromkeys(candidates)), artifacts
