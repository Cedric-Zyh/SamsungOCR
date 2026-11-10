"""Lazy, owned sample resources with an explicit shutdown boundary."""
from threading import Lock
from typing import Callable

from .dependencies import SampleDependencies
from .service import SampleCenter


class SampleRuntime:
    def __init__(self, get_dependencies: Callable[[], SampleDependencies]):
        self._get_dependencies = get_dependencies
        self._lock = Lock()
        self._center = None
        self._key = None

    def get(self) -> SampleCenter:
        with self._lock:
            dependencies = self._get_dependencies()
            key = (dependencies.database.path, dependencies.paths)
            if self._center is None or self._key != key:
                if self._center is not None:
                    self._center.close()
                    self._center = None
                self._center = SampleCenter(dependencies)
                self._key = key
            return self._center

    def close(self):
        with self._lock:
            if self._center is not None:
                self._center.close()
                self._center = None
                self._key = None
