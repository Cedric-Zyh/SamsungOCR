from pathlib import Path
import ast


def test_removed_monolith_entrypoints_are_not_kept():
    root = Path(__file__).resolve().parents[1]
    removed = [
        "receipt_ocr/analyzer.py",
        "receipt_ocr/database.py",
        "receipt_ocr/parser.py",
        "receipt_ocr/pipeline.py",
        "receipt_ocr/job_store.py",
        "receipt_ocr/job_worker.py",
        "receipt_ocr/job_service.py",
        "receipt_ocr/stage_fields.py",
        "receipt_ocr/stage_products.py",
        "receipt_ocr/stage_handwriting.py",
        "receipt_ocr/stage_date.py",
        "receipt_ocr/stage_seal.py",
        "receipt_ocr/recognition_config.py",
        "receipt_ocr/image_processing.py",
        "receipt_ocr/ocr_backends.py",
        "receipt_ocr/paddle_ocr.py",
        "receipt_ocr/field_schema.py",
        "receipt_ocr/field_rules.py",
        "receipt_ocr/product_rules.py",
        "receipt_ocr/document_types.py",
        "receipt_ocr/document_layout.py",
        "receipt_ocr/document_context.py",
        "receipt_ocr/execution.py",
        "receipt_ocr/recognition_progress.py",
        "receipt_ocr/recognition_scope.py",
        "receipt_ocr/recognition_safety.py",
        "static/modules/application.mjs",
        "static/modules/api.mjs",
        "static/modules/state.mjs",
    ]
    assert [path for path in removed if (root / path).exists()] == []


def test_new_boundaries_are_present():
    root = Path(__file__).resolve().parents[1]
    expected = [
        "receipt_ocr/application/pipeline.py",
        "receipt_ocr/application/analyzer.py",
        "receipt_ocr/domain/parsing/__init__.py",
        "receipt_ocr/recognition/date/api.py",
        "receipt_ocr/recognition/date/workflow.py",
        "receipt_ocr/recognition/date/crops.py",
        "receipt_ocr/recognition/date/evidence.py",
        "receipt_ocr/recognition/date/decision.py",
        "receipt_ocr/recognition/date/audit.py",
        "receipt_ocr/recognition/date/slots.py",
        "receipt_ocr/recognition/shared/__init__.py",
        "receipt_ocr/recognition/seal/api.py",
        "receipt_ocr/recognition/seal/workflow.py",
        "receipt_ocr/recognition/seal/evidence.py",
        "receipt_ocr/recognition/seal/decision.py",
        "receipt_ocr/recognition/seal/reading_policy.py",
        "receipt_ocr/recognition/seal/ocr/secondary.py",
        "receipt_ocr/recognition/seal/policy.py",
        "receipt_ocr/recognition/seal/providers/qingtong.py",
        "receipt_ocr/recognition/seal/reference/matcher.py",
        "receipt_ocr/jobs/worker.py",
        "receipt_ocr/persistence/database.py",
        "receipt_ocr/providers/catalog.py",
        "receipt_ocr/providers/paddle_runtime.py",
        "receipt_ocr/imaging/contracts.py",
        "receipt_ocr/imaging/io.py",
        "receipt_ocr/imaging/colors.py",
        "receipt_ocr/imaging/shapes.py",
        "receipt_ocr/imaging/detection.py",
        "receipt_ocr/imaging/page.py",
        "receipt_ocr/imaging/crops.py",
        "receipt_ocr/imaging/date.py",
        "receipt_ocr/imaging/ellipse.py",
        "receipt_ocr/imaging/unwrap.py",
        "receipt_ocr/imaging/bands.py",
        "receipt_ocr/domain/fields/schema.py",
        "receipt_ocr/domain/documents/types.py",
        "receipt_ocr/domain/decision.py",
        "receipt_ocr/recognition/fields/fallbacks.py",
        "receipt_ocr/recognition/products/fallbacks.py",
        "receipt_ocr/application/plans.py",
        "receipt_ocr/application/context.py",
        "receipt_ocr/application/requests.py",
        "receipt_ocr/runtime/execution.py",
        "receipt_ocr/runtime/progress.py",
        "receipt_ocr/runtime/scope.py",
        "receipt_ocr/web/application.py",
        "static/modules/shell/application.mjs",
        "static/modules/core/api.mjs",
        "static/modules/shell/controller.mjs",
        "static/modules/records/controller.mjs",
        "static/modules/review/controller.mjs",
    ]
    assert all((root / path).is_file() for path in expected)


def test_domain_does_not_import_image_or_ocr_infrastructure():
    root = Path(__file__).resolve().parents[1]
    forbidden = {"PIL", "cv2", "numpy", "paddle", "providers", "imaging", "pathlib", "tempfile"}
    violations = []
    for path in (root / "receipt_ocr" / "domain").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in forbidden:
                    violations.append(f"{path.relative_to(root)}: {name}")
    assert violations == []
