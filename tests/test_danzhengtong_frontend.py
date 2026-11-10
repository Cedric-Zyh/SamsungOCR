from pathlib import Path
import shutil
import subprocess

import pytest


def test_independent_remote_buttons_and_query_selection():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    completed = subprocess.run([node, '--test', 'tests/danzhengtong_controls.cjs', 'tests/remote_settings_ui.cjs'],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stdout + completed.stderr
