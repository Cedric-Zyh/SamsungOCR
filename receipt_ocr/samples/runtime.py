"""Lazy, owned sample resources with an explicit shutdown boundary."""
from threading import Lock
from typing import Callable

from .dependencies import SampleDependencies
from .service import SampleCenter, SampleUnavailable


class SampleRuntime:
    def __init__(self, get_dependencies: Callable[[], SampleDependencies]):
        self._get_dependencies = get_dependencies
        self._lock = Lock()
        self._center = None
        self._key = None
        self._owner_token = None
        self._owner_bound = False
        self._accepting = True

    def _close_center(self):
        if self._center is not None:
            if not self._center.close():
                return False
            self._center = None
            self._key = None
        return True

    def bind_owner(self, owner_token) -> bool:
        """Retire the previous center before allowing a new application owner."""
        with self._lock:
            if self._owner_bound and self._owner_token is owner_token and self._accepting:
                return True
            if not self._close_center():
                return False
            self._owner_token = owner_token
            self._owner_bound = True
            self._accepting = True
            return True

    def get(self) -> SampleCenter:
        with self._lock:
            if not self._accepting:
                raise SampleUnavailable('样单运行环境正在关闭')
            dependencies = self._get_dependencies()
            token = dependencies.owner_token
            if self._owner_bound and token is not self._owner_token:
                raise SampleUnavailable('该请求所属的样单运行环境已关闭')
            owner = token if token is not None else dependencies.database
            key = (id(owner), dependencies.database.path, dependencies.paths)
            if self._center is None or self._key != key:
                if not self._close_center():
                    raise SampleUnavailable('样单后台任务尚未退出，不能替换运行环境')
                self._center = SampleCenter(dependencies)
                self._key = key
            elif self._center.stopping or self._center.closed:
                raise SampleUnavailable('样单后台任务正在停止')
            return self._center

    def close(self) -> bool:
        with self._lock:
            self._accepting = False
            return self._close_center()
