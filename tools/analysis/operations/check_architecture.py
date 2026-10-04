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
        "receipt_ocr.application",
        "receipt_ocr.stages",
        "receipt_ocr.persistence",
        "receipt_ocr.recognition",
        "receipt_ocr.runtime",
        "receipt_ocr.jobs",
        "PIL",
        "cv2",
        "numpy",
        "paddle",
        "pathlib",
        "tempfile",
    },
    "providers": {"receipt_ocr.web", "receipt_ocr.application", "receipt_ocr.stages"},
    "imaging": {"receipt_ocr.web", "receipt_ocr.providers", "receipt_ocr.application", "receipt_ocr.stages"},
    "web": {"receipt_ocr.imaging", "receipt_ocr.providers.paddle_runtime"},
    "application": {"receipt_ocr.web", "receipt_ocr.persistence"},
    "stages": {"receipt_ocr.web", "receipt_ocr.persistence", "receipt_ocr.jobs"},
    "persistence": {"receipt_ocr.web", "receipt_ocr.application", "receipt_ocr.stages", "receipt_ocr.providers", "receipt_ocr.imaging", "receipt_ocr.recognition"},
}


def _imports(path: Path, root: Path = PACKAGE) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = (root.name, *path.relative_to(root).parent.parts)
                base = package[:len(package) - node.level + 1]
                module = ".".join((*base, *filter(None, (node.module or "").split("."))))
            else:
                module = node.module or ""
            result.append((node.lineno, module))
            result.extend((node.lineno, f"{module}.{alias.name}")
                          for alias in node.names if alias.name != "*")
    return result


def violations(root: Path = PACKAGE) -> list[str]:
    errors = []
    for layer, forbidden in RULES.items():
        layer_root = root / layer
        if not layer_root.exists():
            continue
        for path in sorted(layer_root.rglob("*.py")):
            for line, imported in _imports(path, root):
                if any(
                    imported == item or imported.startswith(item + ".")
                    for item in forbidden
                ):
                    errors.append(
                        f"{root.name}/{path.relative_to(root)}:{line}: {layer} cannot import {imported}"
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
