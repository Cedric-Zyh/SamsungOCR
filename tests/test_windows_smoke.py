import json
import sys

from tools.windows_smoke import main


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
    assert report["default_backend"] == "paddle"
    assert report["hybrid_route"] == {
        "page": "paddle",
        "date": "paddle",
        "seal": "paddle",
    }
    # The macOS Vision backend is gone: it must not appear in the catalog at all.
    assert all(backend["id"] != "vision" for backend in report["backends"])
