"""Exercise provider-error presentation without a server or live data."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_provider_errors_are_visible_in_records_workbench_and_review():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    result = subprocess.run([node, '--test', 'tests/provider_errors.cjs'],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
