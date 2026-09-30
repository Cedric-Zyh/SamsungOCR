import json
import sys

from tools.analysis.operations.windows_smoke import main


def test_simulated_windows_smoke_writes_auditable_report(tmp_path, monkeypatch):
    output = tmp_path / "windows-smoke.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "windows_smoke.py",
            "--simulate-windows",
            "--output",
            str(output),
        ],
    )

    assert main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["failures"] == []
    assert report["default_backend"] == "paddle_v6"
    assert report["default_route"] == {
        "page": "paddle_v6",
        "date": "paddle_v6",
        "seal": "paddle_v6",
    }
    # The macOS Vision backend is gone: it must not appear in the catalog at all.
    assert all(backend["id"] != "vision" for backend in report["backends"])
