"""Request-local provider allowlist; legacy runs remain unrestricted."""
from contextlib import contextmanager
from contextvars import ContextVar

_allowed = ContextVar('recognition_allowed_providers', default=None)


def provider_allowed(provider):
    allowed = _allowed.get()
    return allowed is None or provider in allowed


def allowed_providers():
    """Current request's allowlist, or ``None`` while the run is unrestricted."""
    allowed = _allowed.get()
    return None if allowed is None else frozenset(allowed)


@contextmanager
def provider_scope(providers):
    token = _allowed.set(set(providers))
    try:
        yield
    finally:
        _allowed.reset(token)
