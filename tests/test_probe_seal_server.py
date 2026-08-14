from pathlib import Path

from receipt_ocr.ocr_types import TextObservation
from tools import probe_seal_server


def _row(text: str, confidence: float = 0.9) -> TextObservation:
    return TextObservation(text, confidence, 0.0, 0.0, 1.0, 1.0)


def test_probe_uses_only_resolved_color_evidence_and_compares_models(
    tmp_path: Path, monkeypatch
):
    evidence = tmp_path / "batch" / "seal-color.png"
    evidence.parent.mkdir()
    evidence.write_bytes(b"probe")
    requirement = "深圳市星睿奇光电有限公司仓储部收货章"

    def fake_recognize(_path, *, model_variant, **_kwargs):
        if model_variant == "server":
            return [_row(requirement)]
        return [_row("仓储部")]

    monkeypatch.setattr(probe_seal_server, "recognize_text", fake_recognize)
    record = {
        "filename": "sample.jpg",
        "seal": {"requirement": requirement, "recognized": "仓储部"},
        "artifacts": {
            "seals": [{
                "color_isolated_url": "/files/artifacts/batch/seal-color.png",
                "original_url": "/files/artifacts/batch/printed-form.jpg",
            }]
        },
    }

    result = probe_seal_server.probe_record(record, tmp_path)

    assert result["mobile_probe_match"]["reliable"] is False
    assert result["existing_plus_mobile_match"]["reliable"] is False
    assert result["regions"][0]["existing_plus_mobile_match"]["reliable"] is False
    assert result["server_probe_match"]["reliable"] is True
    assert result["existing_plus_server_match"]["reliable"] is True
    assert len(result["regions"][0]["variants"]) == 1
    assert result["regions"][0]["variants"][0]["preprocessing"] == "保留章色白底图"


def test_resolve_artifact_url_rejects_non_artifact_and_missing_file(tmp_path: Path):
    assert probe_seal_server.resolve_artifact_url("/uploads/a.png", tmp_path) is None
    assert (
        probe_seal_server.resolve_artifact_url(
            "/files/artifacts/missing.png", tmp_path
        )
        is None
    )
