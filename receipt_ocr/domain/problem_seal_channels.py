"""Unpack independent stamp readings without attributing a merged verdict to a model."""

LOCAL_METHODS = {'paddle_v6', 'paddle_seal', 'paddle', 'paddle_server',
                 'hybrid', 'hybrid_server', 'legacy_local'}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _source(check, variant, hint='unknown'):
    method = _text(variant.get('method')) or _text(check.get('method'))
    if method == 'qingtong':
        return 'qingtong'
    if method in LOCAL_METHODS:
        return 'local'
    if method == 'danzhengtong' or check.get('recognition_mode') == 'danzhengtong':
        return 'unknown'
    if method:  # Other providers must not be guessed to be a local model.
        return 'unknown'
    backend, source = _text(check.get('backend')), _text(check.get('source'))
    if backend == '单证通' or source == '单证通':
        return 'unknown'
    if (check.get('recognition_mode') == 'local' or backend in LOCAL_METHODS
            or backend.startswith(('PaddleOCR', '本地'))):
        return 'local'
    if (check.get('recognition_mode') == 'qingtong_only'
            or backend.startswith('清瞳') or source.startswith('清瞳 ·')):
        return 'qingtong'
    if check.get('comparison_policy') in {'strict_text', 'strict_information_fields'}:
        return 'local'
    return hint


def _enabled(result, source):
    config = _mapping(result.get('recognition_config'))
    if 'seal' not in config:
        return True
    methods = config['seal']
    if not isinstance(methods, list):
        return bool(methods)
    if source == 'qingtong':
        return 'qingtong' in methods
    if source == 'local':
        return any(method in LOCAL_METHODS for method in methods if isinstance(method, str))
    return bool(methods)


def _primary_hint(result):
    config = _mapping(result.get('recognition_config'))
    methods = config.get('seal')
    if isinstance(methods, list) and len(methods) == 1:
        method = methods[0]
        return ('qingtong' if method == 'qingtong' else 'local'
                if isinstance(method, str) and method in LOCAL_METHODS else 'unknown')
    if result.get('seal_recognition_mode') == 'local':
        return 'local'
    if result.get('seal_recognition_mode') == 'qingtong_only':
        return 'qingtong'
    return 'unknown'


def seal_problem_channels(result, executed, failure):
    """Yield (source, label, check, variant), once per distinct independent reading."""
    seen = set()

    def leaf(check, variant, source, role=''):
        if not _enabled(result, source) or not executed(result, 'seal', check, variant):
            return
        label = {'qingtong': '清瞳印章', 'local': '本地印章'}.get(source, '印章（来源未明确）')
        if role:
            label += f' · {role}'
        identity = (source, role, _text(check.get('status')),
                    _text(check.get('recognized')) or _text(check.get('display_text')),
                    check.get('reliable') is False, failure(variant) or failure(check))
        if identity not in seen:
            seen.add(identity)
            yield source, label, check, variant

    def unpack(check, variant, hint='unknown'):
        if any(_text(item.get(key)) in {'skipped', 'pending', 'queued', 'running', 'cancelled',
                                       'not_run', '未执行', '未运行', '未测试', '已跳过', '待识别', '未核对'}
               for item in (check, variant) for key in ('execution_status', 'status')):
            return
        local = _mapping(check.get('local_channel'))
        remote = _mapping(check.get('qingtong_channel'))
        if local:
            # Locating a box via QingTong does not make its local OCR a remote reading.
            method = variant.get('method')
            local_variant = variant if isinstance(method, str) and method in LOCAL_METHODS else {}
            yield from leaf(local, local_variant, 'local')
        candidate = remote or check
        remote_variant = variant if variant.get('method') == 'qingtong' else {'method': 'qingtong'}
        selected = _mapping(_mapping(candidate.get('dual_check')).get('selected'))
        children = []
        for key, role, field, comparison in (
            ('template', '模板', 'label', 'requirement_match'),
            ('ocr', '文字 OCR', 'text', 'comparison'),
        ):
            channel = _mapping(selected.get(key))
            if not channel:
                continue
            combined = {**channel, **_mapping(channel.get(comparison)), 'recognized': channel.get(field, '')}
            if not combined.get('status') and isinstance(channel.get('matched'), bool):
                combined['status'] = '匹配' if channel['matched'] else '不匹配'
            children.append((combined, role))
        if children:
            if not executed(result, 'seal', candidate, remote_variant):
                return
            if failure(candidate) or failure(remote_variant):
                yield from leaf(candidate, remote_variant, 'qingtong')
            else:
                for channel, role in children:
                    yield from leaf(channel, remote_variant, 'qingtong', role)
            return
        if remote:
            yield from leaf(remote, remote_variant, 'qingtong')
            return
        source = _source(check, variant, hint)
        if local and source in {'local', 'unknown'}:
            return  # The parent is a merged verdict, not another independent model.
        yield from leaf(check, variant, source)

    variants = _mapping(result.get('recognition_variants')).get('seal')
    variants = [item for item in variants if isinstance(item, dict)] if isinstance(variants, list) else []
    for variant in variants:
        check = _mapping(_mapping(variant.get('details')).get('seal_check'))
        yield from unpack(check, variant)
    primary = _mapping(result.get('seal_check'))
    if not variants or any(primary.get(key) for key in ('local_channel', 'qingtong_channel', 'dual_check')):
        yield from unpack(primary, {}, _primary_hint(result))
