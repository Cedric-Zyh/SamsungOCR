from pathlib import Path

from receipt_ocr.application import RecognitionService, resolve_recognition_options
from receipt_ocr.runtime import build_runtime_paths
from receipt_ocr.runtime.safety import _apply_single_paddle_safety
from receipt_ocr.storage import ReceiptFileStore


def test_source_checkout_paths_are_derived_from_the_application_root(tmp_path):
    paths = build_runtime_paths(tmp_path, frozen=False)

    assert paths.resource_dir == tmp_path
    assert paths.data_dir == tmp_path / "数据"
    assert paths.storage_dir == tmp_path / "storage"
    assert paths.database_path == tmp_path / "storage" / "results.db"


def test_packaged_paths_use_the_configured_local_storage(tmp_path):
    paths = build_runtime_paths(
        tmp_path,
        frozen=True,
        environ={"LOCALAPPDATA": str(tmp_path / "local"),
                 "SAMSUNG_RECEIPT_DATA_DIR": str(tmp_path / "data")},
    )

    assert paths.storage_dir == (tmp_path / "data").resolve()
    assert paths.upload_dir == (tmp_path / "data" / "uploads").resolve()


def test_file_store_rejects_paths_outside_allowed_roots(tmp_path):
    data = tmp_path / "data"
    uploads = tmp_path / "uploads"
    data.mkdir()
    uploads.mkdir()
    (data / "receipt.jpg").write_bytes(b"receipt")
    store = ReceiptFileStore(data, uploads)

    assert store.source_for_record({"stored_name": "sample:receipt.jpg"}) == (
        data / "receipt.jpg"
    ).resolve()
    assert store.source_for_record({"stored_name": "sample:../secret.jpg"}) is None
    assert store.source_for_record({"stored_name": "../secret.jpg"}) is None


def test_recognition_service_uses_the_same_reference_matcher_for_both_paths():
    calls = []

    class Analyzer:
        def analyze(self, *args, **kwargs):
            calls.append(("legacy", args, kwargs))
            return {"path": "legacy"}

    matcher = object()
    service = RecognitionService(Analyzer(), matcher)
    result = service.recognize(Path("receipt.jpg"))

    assert result == {"path": "legacy"}
    assert calls[0][2]["reference_matcher"] is matcher


def test_recognition_options_decode_saved_json_and_normalize_defaults():
    options = resolve_recognition_options(
        recognition_config='{"fields": ["paddle_v6"]}',
        ocr_backend=None,
        seal_recognition_mode=None,
    )

    assert options.recognition_config["fields"]
    assert options.recognition_config is options.as_dict()["recognition_config"]
    assert options.as_dict()["ocr_backend"]


def test_single_paddle_safety_resolves_domain_seal_normalizer():
    date_check = {
        "status": "匹配",
        "required": "2026-03-27",
        "actual": "2026-03-27",
        "confidence": 0.91,
    }
    seal_check = {
        "comparison_policy": "strict_text",
        "status": "匹配",
        "requirement": "北京京凌科技有限公司海淀第三分公司售后服务专用章",
        "recognized": "北京京凌科技有限公司海淀第三分公司售后服务专用章",
        "confidence": 0.90,
    }

    policy = _apply_single_paddle_safety(
        date_check,
        seal_check,
        {"page": "paddle_v6", "date": "paddle_v6", "seal": "paddle_v6"},
    )

    assert policy
    assert date_check["reliable"] is True
    assert seal_check["reliable"] is True
