"""Per-run, explicitly saved seal-layout choices; never infer a choice."""
from contextlib import contextmanager
from contextvars import ContextVar

from receipt_ocr.domain.seal_standards import requirement_key

_STANDARDS = ContextVar('seal_layout_standards', default={})
_ACTIVE = ContextVar('active_seal_layout_standard', default=None)
HORIZONTAL_TEXT_VALUES = {'unmarked', 'present', 'absent'}


@contextmanager
def seal_layout_settings_scope(standards):
    # Freeze the settings for this recognition run, including asynchronous
    # stages that copy the current context. A later edit affects the next run.
    snapshot = {requirement_key(item.get('requirement', '')): {
        key: item.get(key) for key in ('id', 'revision', 'shape', 'horizontal_text')
    } for item in standards if item.get('shape') in {'round', 'ellipse'}}
    token = _STANDARDS.set(snapshot)
    try:
        yield
    finally:
        _STANDARDS.reset(token)


@contextmanager
def seal_requirement_layout(requirement):
    token = _ACTIVE.set(_STANDARDS.get().get(requirement_key(requirement)))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def explicit_horizontal_text_policy(shape):
    standard = _ACTIVE.get()
    if (not standard or shape not in {'round', 'ellipse'} or standard.get('shape') != shape
            or standard.get('horizontal_text') not in {'present', 'absent'}):
        return None
    return {'setting': standard['horizontal_text'], 'standard_id': standard['id'],
            'standard_revision': standard['revision'], 'source': 'manual_standard_setting'}
