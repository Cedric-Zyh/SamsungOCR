"""Snapshot the providers explicitly selected for a sample test."""
from copy import deepcopy

from receipt_ocr.application import resolve_recognition_options
from receipt_ocr.domain.recognition import options_with_current_settings
from receipt_ocr.domain.stages import STAGES


SAMPLE_EXECUTION_LABELS = {
    'local': '只本地', 'qingtong': '只清瞳',
    'danzhengtong': '只单证通', 'both': '本地 + 单证通', 'all': '全部一起',
}


def sample_execution_options(settings, mode, *, api_enabled):
    # Older clients omit the mode and continue to use their saved plan.
    if mode is None:
        return resolve_recognition_options(settings=settings, api_enabled=api_enabled).as_dict()
    if not isinstance(mode, str) or mode not in SAMPLE_EXECUTION_LABELS:
        raise ValueError('请选择有效的样单测试方式')
    current = options_with_current_settings({}, settings)
    plan = deepcopy(current['recognition_config'])
    for stage in STAGES:
        plan[stage] = [method for method in plan.get(stage, [])
                       if method not in ('qingtong', 'danzhengtong')] if mode in ('local', 'both', 'all') else []
    if mode in ('local', 'both', 'all') and not any(plan[stage] for stage in STAGES):
        raise ValueError('当前方案未选择本地识别内容，请先在设置中选择')
    if mode in ('qingtong', 'all'):
        plan['seal'].append('qingtong')
    if mode in ('danzhengtong', 'both', 'all'):
        for stage in ('fields', 'handwriting', 'date', 'seal'):
            plan[stage].append('danzhengtong')
    options = resolve_recognition_options(
        recognition_config=plan, ocr_backend=current['ocr_backend'],
        seal_recognition_mode=current['seal_recognition_mode'], api_enabled=api_enabled,
    ).as_dict()
    return {**options, '_sample_execution_mode': mode, '_independent_dzt': True,
            '_execution_scope': 'danzhengtong' if mode == 'danzhengtong' else 'local' if mode == 'local' else 'all'}
