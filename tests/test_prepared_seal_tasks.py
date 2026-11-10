"""Execution instructions survive independent edits to display evidence."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal.contracts import PreparedSeal
from receipt_ocr.recognition.seal.input_evidence import SealEvidence


def prepared_input(*, rows=None):
    entry = {"id": "seal-2:center", "name": "center", "channel": "center",
             "image_url": "/files/center.png", "read_mode": "line",
             "verified_type_read": {"text": "收货专用章", "confidence": .96,
                                    "image_url": "/files/verified.png"}}
    if rows is not None:
        entry["detected_rows"] = rows
    artifact = {"index": 2, "region_id": "seal-2", "shape_code": "round",
                "inputs": [entry], "reads": [], "errors": []}
    return artifact, entry, PreparedSeal.from_inputs(artifact, [(entry, Path("center.png"))])


def test_tasks_snapshot_reused_rows_and_verified_read_without_sharing_display_dictionaries():
    row = {"text": "收货章", "confidence": .99, "x": 0, "y": 0, "width": 1, "height": 1}
    original, entry, prepared = prepared_input(rows=[row])
    task, = prepared.tasks
    row["text"] = "原始记录变更"
    entry["verified_type_read"]["text"] = "另一读数"
    prepared.artifact["inputs"][0].update(channel="body", read_mode="page")
    assert task.channel == "center" and task.read_mode == "line"
    assert task.observations[0].text == "收货章"
    assert task.verified_type_read.text == "收货专用章"
    assert original["inputs"][0]["channel"] == "center"
    with pytest.raises(FrozenInstanceError):
        task.channel = "body"


def test_empty_reused_detection_is_distinct_from_requesting_ocr():
    assert prepared_input(rows=[])[2].tasks[0].observations == ()
    assert prepared_input()[2].tasks[0].observations is None


def test_evidence_records_task_provenance_and_selected_image_by_id():
    original, _, prepared = prepared_input()
    task, = prepared.tasks
    evidence = SealEvidence(prepared)
    evidence.complete(task, [TextObservation("收货专用章", .96, 0, 0, 1, 1)], "paddle_v6",
                      selected_image_url="/files/verified.png")
    assert prepared.artifact["inputs"][0]["status"] == "completed"
    assert prepared.artifact["reads"][0] == {
        "id": "seal-2:center:0", "input_id": "seal-2:center", "region_id": "seal-2",
        "channel": "center", "provider": "paddle_v6", "text": "收货专用章",
        "confidence": .96, "image_url": "/files/verified.png",
    }
    assert prepared.artifact["round_type_band_text"] == "收货专用章"
    assert original["reads"] == [] and "status" not in original["inputs"][0]
