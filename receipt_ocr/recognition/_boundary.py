"""Small module proxy used by feature boundaries.

The feature modules expose a stable import location while preserving live
module identity for instrumentation and test doubles.
"""
from __future__ import annotations

import sys
import types


def install_boundary(name: str, modules: tuple[types.ModuleType, ...], *, extra=None):
    module = sys.modules[name]

    class Boundary(types.ModuleType):
        def __getattr__(self, attr):
            for implementation in modules:
                if hasattr(implementation, attr):
                    return getattr(implementation, attr)
            raise AttributeError(attr)

        def __setattr__(self, attr, value):
            for implementation in modules:
                if hasattr(implementation, attr):
                    setattr(implementation, attr, value)
                    return
            super().__setattr__(attr, value)

        def __dir__(self):
            names = set(super().__dir__())
            for implementation in modules:
                names.update(dir(implementation))
            return sorted(names)

    module.__class__ = Boundary
    names = set(extra or ())
    for implementation in modules:
        names.update(name for name in dir(implementation) if not name.startswith("__"))
    module.__all__ = sorted(names)
    return module
