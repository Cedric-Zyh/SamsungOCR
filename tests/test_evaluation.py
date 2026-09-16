import json

import pytest

from receipt_ocr.evaluation import (
    GROUND_TRUTH_FIELDS,
    build_ground_truth_entry,
    evaluate_backends,
    evaluate_results,
    save_ground_truth_entry,
)
from receipt_ocr.parser import PRODUCT_COLUMNS


def test_product_accuracy_is_measured_per_cell_and_row():
    truth = {
        "sample.jpg": {
            "fields": {},
            "product_rows": [{
                "行号": "10", "产品类别": "G1", "物料编号": "SM-X 白色",
                "等级": "A", "出库仓库": "W002", "数量": "1",
                "重量": "0.488", "体积": "0.001", "EAN码": "1234567890123",
            }],
            "actual_date": "2025-01-05",
            "seal_should_match": True,
        }
    }
    result = {
        "id": 1,
        "filename": "sample.jpg",
        "fields": {},
        "product_table": {"rows": [{"values": {
            **truth["sample.jpg"]["product_rows"][0], "物料编号": "SM-X 黑色",
        }}]},
        "date_check": {"actual": "2025-01-05"},
        "seal_check": {"status": "匹配", "score": 1},
    }
    accuracy = evaluate_results([result], truth)
    assert accuracy["product_cell_correct"] == 8
    assert accuracy["product_cell_total"] == 9
    assert accuracy["product_row_correct"] == 0
    assert accuracy["product_row_total"] == 1
    material = next(item for item in accuracy["product_by_column"] if item["column"] == "物料编号")
    assert material["accuracy"] == 0


def test_ground_truth_entry_is_strict_and_atomically_saved(tmp_path):
    result = {
        "fields": {name: f"真值-{name}" for name in GROUND_TRUTH_FIELDS},
        "date_check": {"actual": "2025-01-05"},
        "product_table": {"rows": [{
            "values": {name: f"值-{name}" for name in PRODUCT_COLUMNS}
        }]},
    }
    entry = build_ground_truth_entry(result, seal_should_match=False)
    assert entry["seal_should_match"] is False
    truth_path = tmp_path / "ground_truth.json"
    change = save_ground_truth_entry(truth_path, "sample.jpg", entry)
    assert change["before"] is None
    assert json.loads(truth_path.read_text(encoding="utf-8"))["sample.jpg"] == entry
    assert not truth_path.with_suffix(".json.tmp").exists()


def test_ground_truth_rejects_missing_human_date():
    result = {
        "fields": {name: "值" for name in GROUND_TRUTH_FIELDS},
        "date_check": {"actual": ""},
        "product_table": {"rows": [{"values": {name: "值" for name in PRODUCT_COLUMNS}}]},
    }
    with pytest.raises(ValueError, match="实际收货日期"):
        build_ground_truth_entry(result, seal_should_match=True)


def test_ground_truth_can_explicitly_record_a_verified_blank_date_line():
    result = {
        "fields": {name: "值" for name in GROUND_TRUTH_FIELDS},
        "date_check": {"actual": ""},
        "product_table": {"rows": [{"values": {name: "值" for name in PRODUCT_COLUMNS}}]},
    }
    entry = build_ground_truth_entry(
        result, seal_should_match=True, date_present=False
    )
    assert entry["actual_date"] == ""
    assert entry["date_present"] is False


def test_date_metrics_treat_verified_blank_date_as_correct_abstention():
    truth = {
        "blank.jpg": {
            "fields": {"要求到货": "2025-06-25"},
            "product_rows": [],
            "actual_date": "",
            "date_present": False,
            "seal_should_match": True,
        }
    }
    result = {
        "id": 1,
        "filename": "blank.jpg",
        "fields": {"要求到货": "2025-06-25"},
        "product_table": {"rows": []},
        "date_check": {"actual": "", "status": "未识别", "reliable": False},
        "seal_check": {"status": "匹配", "score": 1, "reliable": True},
    }
    accuracy = evaluate_results([result], truth)
    assert accuracy["date_correct"] == 1
    assert accuracy["date_absent_correct"] == 1
    assert accuracy["date_absent_accuracy"] == 1
    assert accuracy["date_present_total"] == 0


def test_backend_comparison_keeps_backends_separate():
    truth = {
        "sample.jpg": {
            "fields": {"客户名称": "正确客户"}, "product_rows": [],
            "actual_date": "2025-01-05", "seal_should_match": True,
        }
    }
    common = {
        "filename": "sample.jpg", "product_table": {"rows": []},
        "date_check": {"actual": "2025-01-05"},
        "seal_check": {"status": "匹配", "score": 1},
    }
    rows = [
        {**common, "id": 1, "ocr_backend": "paddle", "processing_seconds": 20,
         "fields": {"客户名称": "正确客户"}},
        {**common, "id": 2, "ocr_backend": "vision", "processing_seconds": 2,
         "fields": {"客户名称": "错误客户"}},
    ]
    comparison = {item["backend"]: item for item in evaluate_backends(rows, truth)}
    assert comparison["paddle"]["field_accuracy"] == 1
    assert comparison["vision"]["field_accuracy"] == 0
    assert comparison["paddle"]["average_seconds"] == 20
    assert comparison["paddle"]["timed_samples"] == 1
    for metrics in comparison.values():
        assert metrics["field_total"] == 1
        assert metrics["product_cell_total"] == 0
        assert metrics["date_total"] == 1
        assert metrics["seal_total"] == 1


def test_backend_report_exposes_zero_denominators_for_unlabeled_results():
    metrics = evaluate_backends([
        {"id": 1, "filename": "unlabeled.jpg", "ocr_backend": "vision"}
    ], {})[0]
    assert metrics["samples"] == 0
    assert metrics["timed_samples"] == 0
    for name in ("field_total", "product_cell_total", "date_total", "seal_total"):
        assert metrics[name] == 0


def test_safe_decision_metrics_separate_abstention_from_wrong_decision():
    truth = {
        "match.jpg": {
            "fields": {"要求到货": "2025-01-05"}, "product_rows": [],
            "actual_date": "2025-01-05", "seal_should_match": True,
        },
        "review.jpg": {
            "fields": {"要求到货": "2025-01-06"}, "product_rows": [],
            "actual_date": "2025-01-05", "seal_should_match": False,
        },
    }
    rows = [
        {
            "id": 1, "filename": "match.jpg", "fields": {"要求到货": "2025-01-05"},
            "product_table": {"rows": []},
            "date_check": {"actual": "2025-01-05", "status": "匹配", "reliable": True},
            "seal_check": {"status": "匹配", "score": 0.9, "reliable": True},
        },
        {
            "id": 2, "filename": "review.jpg", "fields": {"要求到货": "2025-01-06"},
            "product_table": {"rows": []},
            "date_check": {"actual": "", "status": "无法判断", "reliable": False},
            "seal_check": {"status": "无法判断", "score": 0.6, "reliable": False},
        },
    ]
    accuracy = evaluate_results(rows, truth)
    assert accuracy["date_accuracy"] == 0.5
    assert accuracy["date_decision_coverage"] == 0.5
    assert accuracy["date_decision_accuracy"] == 1
    assert accuracy["seal_conclusion_accuracy"] == 0.5
    assert accuracy["seal_decision_coverage"] == 0.5
    assert accuracy["seal_decision_accuracy"] == 1


def test_legacy_truth_reports_missing_new_fields_without_inventing_labels():
    from receipt_ocr.field_schema import OUTPUT_FIELDS
    fields = {name: '正确' for name in GROUND_TRUTH_FIELDS}
    truth = {'sample.jpg': {'fields': fields, 'actual_date': '2025-01-05', 'seal_should_match': True}}
    result = {'id': 1, 'filename': 'sample.jpg', 'field_schema_version': 1,
              'fields': {name: fields.get(name, '') for name in OUTPUT_FIELDS},
              'date_check': {'actual': '2025-01-05'}, 'seal_check': {'status': '匹配'}}
    accuracy = evaluate_results([result], truth)
    assert accuracy['field_accuracy'] == 1
    assert accuracy['field_total'] == 3
    coverage = accuracy['field_coverage']
    assert set(coverage['evaluated_fields']) == {'客户名称', '要求到货', '签章要求'}
    assert coverage['separately_evaluated_fields'] == ['签收日期']
    assert set(coverage['unlabeled_fields']) == {'仓库联系人', '仓库接收人', '实收数量', '拒收数量', '合计数量'}


def test_explicitly_labeled_blank_fields_count_as_evaluated():
    truth = {'sample.jpg': {'fields': {'拒收数量': ''}, 'actual_date': '', 'date_present': False}}
    result = {'filename': 'sample.jpg', 'field_schema_version': 1, 'fields': {'拒收数量': ''}}
    metrics = evaluate_results([result], truth)
    assert metrics['field_correct'] == 1
    assert '拒收数量' in metrics['field_coverage']['evaluated_fields']
    assert '拒收数量' not in metrics['field_coverage']['unlabeled_fields']
