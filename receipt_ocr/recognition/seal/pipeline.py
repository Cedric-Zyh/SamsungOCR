"""One stamp -> prepared inputs -> OCR observations and ring ordering.

The requirement is used only after OCR as a soft ranking signal for circular
seam ordering. It never supplies missing characters or replaces raw OCR.
"""
from contextlib import nullcontext
from pathlib import Path
import tempfile

from receipt_ocr.runtime.execution import timed
from .preprocess.inputs import prepare_inputs
from .ocr.interface import OcrReader
from .postprocess.ring_text import reorder_ring_text


@timed("local_seal_recognition")
def _recognize_local_seals(source, rows, regions, artifact_dir, artifact_url_prefix,
                           ocr_backend, secondary_ocr_backend=None, requirement="",
                           footer_anchor_y=None, orientation_mode="polygon"):
    reader = OcrReader()
    context = (nullcontext(str(Path(artifact_dir) / "seals")) if artifact_dir
               else tempfile.TemporaryDirectory(prefix="receipt-seals-"))
    prefix = f"{artifact_url_prefix.rstrip('/')}/seals" if artifact_url_prefix else ""
    texts, artifacts = [], []
    with context as directory:
        for index, region in enumerate(regions):
            try:
                artifact, jobs = prepare_inputs(Path(source), region, index, directory, prefix,
                                               ocr_backend, orientation_mode)
            except Exception as exc:
                artifacts.append({"schema_version": 2, "index": index, "region_id": f"seal-{index}",
                                  "color": region.color, "inputs": [], "reads": [],
                                  "errors": [{"step": "prepare", "message": str(exc)}]})
                continue
            for input_image, path in jobs:
                try:
                    # A ring is one revolution, not three alternative rows.
                    observations = (reader.read(path, provider=ocr_backend) if input_image["channel"] == "body"
                                    else reader.read_line(path, provider=ocr_backend))
                    input_image["status"] = "completed" if observations else "empty"
                    for position, row in enumerate(observations):
                        if not row.text:
                            continue
                        read = {"id": f"{input_image['id']}:{position}", "input_id": input_image["id"],
                                "region_id": artifact["region_id"], "channel": input_image["channel"],
                                "provider": ocr_backend, "text": row.text, "confidence": row.confidence,
                                "image_url": input_image["image_url"]}
                        artifact["reads"].append(read)
                        texts.append(row.text)
                        channel_texts = [
                            item["text"] for item in artifact["reads"]
                            if item.get("channel") == input_image["channel"]
                        ]
                        if input_image["channel"] == "ring":
                            artifact["unwrap_texts"] = channel_texts
                            artifact["unwrapped_text"] = " | ".join(channel_texts)
                        elif input_image["channel"] == "center":
                            artifact["round_type_band_texts"] = channel_texts
                            artifact["round_type_band_text"] = " | ".join(channel_texts)
                        elif input_image["channel"] == "body":
                            artifact["color_isolated_texts"] = channel_texts
                            artifact["color_isolated_text"] = " | ".join(channel_texts)
                except Exception as exc:
                    input_image.update(status="failed", error=str(exc))
                    artifact["errors"].append({"step": input_image["channel"], "message": str(exc)})
            ring_reads = [
                item["text"] for item in artifact["reads"]
                if item.get("channel") == "ring" and item.get("text")
            ]
            if ring_reads:
                raw_ring_text = " | ".join(ring_reads)
                reorder = reorder_ring_text(
                    ring_reads[0], requirement=requirement, alternatives=ring_reads[1:]
                )
                artifact["unwrapped_raw_text"] = raw_ring_text
                artifact["ring_reorder"] = reorder
                artifact["unwrapped_text"] = reorder["text"]
                if reorder["changed"]:
                    texts.append(reorder["text"])
            artifacts.append(artifact)
    return texts, artifacts
