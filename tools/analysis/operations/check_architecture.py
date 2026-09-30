"""Check import direction between the receipt recognition layers."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "receipt_ocr"

RULES = {
    "domain": {
        "receipt_ocr.providers",
        "receipt_ocr.imaging",
        "receipt_ocr.web",
        "PIL",
        "cv2",
        "numpy",
        "paddle",
        "pathlib",
        "tempfile",
    },
    "providers": {"receipt_ocr.web"},
    "imaging": {"receipt_ocr.web", "receipt_ocr.providers"},
    "web": {"receipt_ocr.imaging", "receipt_ocr.providers.paddle_runtime"},
}


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            result.append((node.lineno, node.module or ""))
    return result


def violations(root: Path = PACKAGE) -> list[str]:
    errors = []
    for layer, forbidden in RULES.items():
        layer_root = root / layer
        if not layer_root.exists():
            continue
        for path in sorted(layer_root.rglob("*.py")):
            for line, imported in _imports(path):
                if any(
                    imported == item or imported.startswith(item + ".")
                    for item in forbidden
                ):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{line}: {layer} cannot import {imported}"
                    )
    return errors


def main() -> int:
    errors = violations()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("architecture import direction: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
