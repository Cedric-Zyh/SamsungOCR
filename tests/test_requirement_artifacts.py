from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from receipt_ocr.field_rules import _recover_signature_requirement
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.pipeline import merge_stage


def test_retry_saves_exact_input_even_when_original_value_is_kept(tmp_path, monkeypatch):
    from receipt_ocr import paddle_ocr

    source = tmp_path / "page.png"
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 477, 300, 488), fill="red")
    draw.rectangle((580, 494, 630, 510), fill="black")
    image.save(source)
    value = "北京集中维修中心业务章（3）"
    rows = [TextObservation("签章要求：" + value, .97, .03, .477, .3, .013)]
    observed = []

    def recognize(path, **kwargs):
        observed.append(Path(path).read_bytes())
        return [TextObservation("签章要求：" + value, .99, 0, 0, 1, 1)]

    monkeypatch.setattr(paddle_ocr, "recognize_line", recognize)
    artifacts = []
    result = _recover_signature_requirement(
        source, rows, value, "paddle_v6", artifact_dir=tmp_path / "artifacts",
        artifact_url_prefix="/files/artifacts/test", artifacts=artifacts,
    )
    assert result is None  # Equal-length retry must not replace the primary.
    artifact = artifacts[0]
    directory = tmp_path / "artifacts" / "requirements"
    assert (directory / "signature-requirement.jpg").read_bytes() == observed[0]
    with Image.open(directory / "signature-requirement-original.png") as original:
        assert original.size == (625, 19)
        assert original.getpixel((20, 8)) == (255, 0, 0)
    with Image.open(directory / "signature-requirement.jpg") as clean:
        assert clean.getextrema() == (255, 255)  # Red removed; next row excluded.
    assert artifact["ocr_text"] == "签章要求：" + value
    assert artifact["original_url"].startswith("/files/artifacts/test/requirements/")


def test_legacy_merge_keeps_requirement_images_separate_from_seals():
    artifact = {"original_url": "/files/artifacts/test/requirements/original.png"}
    output = {"processing_artifacts": {"date": [], "seals": []}}
    merge_stage(output, {"processing_artifacts": {"signature_requirement": [artifact]}})
    merge_stage(output, {"processing_artifacts": {"seals": [{"index": 0}]}})
    assert output["processing_artifacts"]["signature_requirement"] == [artifact]


def test_configured_fields_keep_requirement_images_without_any_seal_stage(monkeypatch):
    from receipt_ocr import recognition_config as config
    from receipt_ocr.document_context import DocumentContext

    artifact = {"original_url": "/files/artifacts/test/requirements/original.png"}
    stage = {"fields": {"签章要求": "客户收货章"}, "processing_artifacts": {
        "signature_requirement": [artifact]}}
    monkeypatch.setattr(config, "validate_config", lambda value, **kw: value)
    monkeypatch.setattr(config, "_recognize_stages", lambda *a: (
        {**{name: [] for name in config.STAGES},
         "fields": [{"method": "paddle_v6", "result": stage}]}, [], stage, {}))
    monkeypatch.setattr(config, "complete_result", lambda *a, **kw: None)
    monkeypatch.setattr(DocumentContext, "evidence", lambda self: {})
    result = config.run_configured(
        SimpleNamespace(seal_api=SimpleNamespace(enabled=False)), "unused.jpg", None,
        config={"fields": ["paddle_v6"], "products": [], "handwriting": [], "date": [], "seal": []},
    )
    assert result["processing_artifacts"]["signature_requirement"] == [artifact]
    assert result["recognition_variants"]["fields"][0]["details"]["processing_artifacts"]["signature_requirement"] == [artifact]
