import pytest

from receipt_ocr.backend_benchmark import compare_backend_runs


def result(backend: str, material: str, *, seconds: float = 1.0) -> dict:
    return {
        "filename": "sample.jpg",
        "ocr_backend": backend,
        "processing_seconds": seconds,
        "fields": {"客户订单号": "123"},
        "product_table": {"rows": [{"values": {"行号": "10", "物料编号": material}}]},
        "date_check": {"actual": "2025-01-01", "status": "匹配", "reliable": True},
        "seal_check": {"status": "匹配", "reliable": True},
    }


def test_backend_benchmark_reports_truth_metrics_and_reference_agreement():
    truth = {
        "sample.jpg": {
            "fields": {"客户订单号": "123"},
            "product_rows": [{"行号": "10", "物料编号": "A0"}],
            "actual_date": "2025-01-01",
            "seal_should_match": True,
        }
    }
    report = compare_backend_runs(
        {
            "mobile": [result("mobile", "A0", seconds=2)],
            "server": [result("server", "AO", seconds=4)],
        },
        truth,
        {"documents": {}, "logical_receipts": {}},
        reference_backend="mobile",
    )

    by_backend = {item["backend"]: item for item in report["backends"]}
    assert by_backend["mobile"]["receipt_metrics"]["product_cell_accuracy"] == 1.0
    assert by_backend["server"]["receipt_metrics"]["product_cell_accuracy"] == .5
    assert by_backend["mobile"]["product_agreement_with_reference"]["rate"] == 1.0
    assert by_backend["server"]["product_agreement_with_reference"]["rate"] == .5
    assert by_backend["server"]["average_seconds"] == 4.0


def test_backend_benchmark_requires_reference_backend():
    with pytest.raises(ValueError, match="缺少参考后端"):
        compare_backend_runs({}, {}, {}, reference_backend="mobile")


def test_backend_benchmark_keeps_last_duplicate_filename_run():
    truth = {
        "sample.jpg": {
            "fields": {"客户订单号": "123"},
            "product_rows": [{"行号": "10", "物料编号": "A0"}],
            "actual_date": "2025-01-01",
            "seal_should_match": True,
        }
    }
    first = result("server", "AO", seconds=100)
    rerun = result("server", "A0", seconds=10)
    report = compare_backend_runs(
        {
            "mobile": [result("mobile", "A0", seconds=2)],
            "server": [first, rerun],
        },
        truth,
        {"documents": {}, "logical_receipts": {}},
        reference_backend="mobile",
    )

    server = next(item for item in report["backends"] if item["backend"] == "server")
    assert server["files"] == 1
    assert server["average_seconds"] == 10.0
    assert server["receipt_metrics"]["product_cell_accuracy"] == 1.0
    assert server["product_agreement_with_reference"]["rate"] == 1.0
