"""Browser-handler regressions run without a browser, network, or live storage."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_node(*args):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    result = subprocess.run(
        [node, *args], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_frontend_request_selection_confirmation_and_pagination_behaviors():
    run_node("--test", "tests/frontend_behaviors.cjs")


def test_review_readiness_keyboard_and_image_interactions():
    run_node("--test", "tests/review_ui_behaviors.cjs", "tests/review_modal_navigation.cjs")


def test_second_round_ui_recovery_and_feedback_behaviors():
    run_node("--test", "tests/records_ui_round2.cjs", "tests/report_ui_round2.cjs",
             "tests/review_ui_round2.cjs")


def test_settings_plan_presets_validation_and_persistence():
    run_node("--test", "tests/settings_ui.cjs")


def test_workbench_todos_pagination_and_record_navigation():
    run_node("--test", "tests/workbench_redesign.cjs", "tests/workbench_records_entry.cjs",
             "tests/import_calendar.cjs")


def test_qingtong_selected_stamp_preview_sources():
    run_node("--test", "tests/qingtong_preview.cjs")


def test_rendered_attributes_preserve_quotes_without_creating_event_handlers():
    samples = json.loads(run_node("tests/frontend_behaviors.cjs", "--render-samples"))

    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            assert not any(name.startswith("on") for name in attributes)
            self.tags.append((tag, attributes))

    record = Elements()
    record.feed(samples["markup"])
    assert any(attrs.get("title") == samples["filename"] for _, attrs in record.tags)
    assert any(attrs.get("title") == samples["customer"] for _, attrs in record.tags)
    products = Elements()
    products.feed(samples["productMarkup"])
    assert next(attrs["value"] for tag, attrs in products.tags if tag == "input") == samples["product"]
    assert samples["unsafeArtifact"] == ""
    artifact = Elements()
    artifact.feed(samples["artifact"])
    assert next(attrs["src"] for tag, attrs in artifact.tags if tag == "img") == '/files/a" onerror="alert(3).jpg'
