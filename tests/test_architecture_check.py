from tools.analysis.operations.check_architecture import violations
import pytest


def test_import_direction_is_clean():
    assert violations() == []


@pytest.mark.parametrize("statement", [
    "from ..providers import catalog",
    "from receipt_ocr import imaging",
    "from receipt_ocr.imaging import io",
    "import receipt_ocr.providers.catalog as catalog",
    "def late_import():\n    from receipt_ocr.web import application",
])
def test_forbidden_imports_fail_regardless_of_spelling(tmp_path, statement):
    root = tmp_path / "receipt_ocr"
    (root / "domain").mkdir(parents=True)
    (root / "domain" / "example.py").write_text(statement)
    assert violations(root)


def test_relative_domain_import_and_nested_package_are_resolved(tmp_path):
    root = tmp_path / "receipt_ocr"
    folder = root / "domain" / "fields"
    folder.mkdir(parents=True)
    path = folder / "__init__.py"
    path.write_text("from ..ocr import TextObservation")
    assert violations(root) == []
    path.write_text("from ...providers import catalog")
    assert violations(root)


@pytest.mark.parametrize("layer, statement", [
    ("samples", "from receipt_ocr.web import application"),
    ("samples", "from flask import abort"),
    ("samples", "from werkzeug.exceptions import NotFound"),
    ("persistence", "from ..jobs.danzhengtong_store import DanzhengtongOperations"),
    ("jobs", "from ..samples.jobs import SampleJobStore"),
    ("application", "from receipt_ocr.samples.service import SampleCenter"),
    ("domain", "from receipt_ocr.samples.service import SampleCenter"),
])
def test_business_and_storage_boundaries_reject_upward_imports(tmp_path, layer, statement):
    root = tmp_path / "receipt_ocr"
    (root / layer).mkdir(parents=True)
    (root / layer / "example.py").write_text(statement)
    assert violations(root)
