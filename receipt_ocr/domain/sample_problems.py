"""Human observations: sample features persist; recognition problems belong to a test."""
from copy import deepcopy


COMMON_PROBLEM_TYPES = (
    '日期读错', '日期漏识别', '空白误识别日期',
    '基础字段错误', '商品明细错误', '签名识别问题',
    '原单日期不符', '原单印章不符', '证据不足待核对', '识别执行失败', '其他问题',
)
SEAL_PROBLEM_SUFFIXES = ('未检出', '文字缺漏或读错', '判定错误', '证据不足', '识别失败')
QINGTONG_PROBLEM_TYPES = tuple(f'清瞳印章{suffix}' for suffix in SEAL_PROBLEM_SUFFIXES)
LOCAL_PROBLEM_TYPES = tuple(f'本地印章{suffix}' for suffix in SEAL_PROBLEM_SUFFIXES)
# Historical and unknown-source observations keep their original attribution.
GENERIC_SEAL_PROBLEM_TYPES = ('印章未检出', '印章文字缺漏或读错', '印章判定错误')
PROBLEM_TYPES = COMMON_PROBLEM_TYPES + QINGTONG_PROBLEM_TYPES + LOCAL_PROBLEM_TYPES + GENERIC_SEAL_PROBLEM_TYPES
PROBLEM_GROUPS = (
    ('日期与其他', COMMON_PROBLEM_TYPES),
    ('清瞳印章', QINGTONG_PROBLEM_TYPES),
    ('本地印章', LOCAL_PROBLEM_TYPES),
)
SAMPLE_FEATURES = (
    '模糊', '遮挡', '印章过淡', '倾斜或旋转', '裁切不完整', '版式变化',
    '图像质量问题', '其他图像问题',
)
PROBLEM_STATUSES = {'unmarked', 'clear', 'issues'}
LEGACY_FEATURES = {'图像质量问题', '版式变化'}


def _saved_tags(value):
    """Read historical JSON defensively without guessing extra tags from free text."""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value
                             if isinstance(item, str) and item.strip() and len(item) <= 200))


def resolve_problem_annotations(entry, result_id=None):
    legacy = entry.get('error_type', '')
    legacy = legacy.strip() if isinstance(legacy, str) else ''
    features = _saved_tags(entry.get('sample_features')) if 'sample_features' in entry else (
        [legacy] if legacy in LEGACY_FEATURES else [])
    reviews = entry.get('problem_reviews')
    linked = entry.get('legacy_problem_links')
    linked = linked if isinstance(linked, dict) else {}
    adopted = bool(linked.get(legacy)) or any(
        isinstance(item, dict) and item.get('status') == 'issues' and legacy in _saved_tags(item.get('types'))
        for item in (reviews.values() if isinstance(reviews, dict) else []))
    review = reviews.get(str(result_id), {}) if isinstance(reviews, dict) and result_id is not None else {}
    review = review if isinstance(review, dict) else {}
    status = review.get('status', 'unmarked')
    types = _saved_tags(review.get('types'))
    # Inconsistent historical data must not claim that a test has been checked.
    if (not isinstance(status, str) or status not in PROBLEM_STATUSES
            or (status == 'issues' and not types) or (status != 'issues' and types)):
        status = 'unmarked'
    return {
        'problem_review': {'status': status, 'types': types if status == 'issues' else []},
        'sample_features': features,
        'legacy_problem_types': [legacy] if legacy and legacy not in LEGACY_FEATURES and not adopted else [],
    }


def _validate_tags(value, allowed, label):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError(f'{label}必须为最多 32 项的列表')
    tags = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 200:
            raise ValueError(f'{label}每项须为 1–200 字')
        item = item.strip()
        if item not in allowed:
            raise ValueError(f'未知{label}：{item}')
        if item not in tags:
            tags.append(item)
    return tags


def update_problem_annotations(entry, payload, *, result_id, context):
    """Validate a patch in memory; the caller saves it in the annotation transaction."""
    resolved = resolve_problem_annotations(entry, result_id)
    if 'sample_features' in payload:
        entry['sample_features'] = _validate_tags(
            payload['sample_features'], set(SAMPLE_FEATURES) | set(resolved['sample_features']), '图片与版式特征')
    if 'problem_status' not in payload and 'problem_types' not in payload:
        return
    previous = resolved['problem_review']
    allowed = set(PROBLEM_TYPES) | set(previous['types']) | set(resolved['legacy_problem_types'])
    types = _validate_tags(payload.get('problem_types', previous['types']), allowed, '问题类型')
    status = payload.get('problem_status', 'issues' if types else previous['status'])
    if not isinstance(status, str) or status not in PROBLEM_STATUSES:
        raise ValueError('请选择有效的问题标注状态')
    if status == 'issues' and not types:
        raise ValueError('已发现问题时，请至少选择一项问题类型')
    if status != 'issues' and types:
        raise ValueError('尚未标注或已核对无问题时，不能同时选择问题类型')
    reviews = entry.get('problem_reviews')
    reviews = deepcopy(reviews) if isinstance(reviews, dict) else {}
    legacy = entry.get('error_type')
    legacy = legacy.strip() if isinstance(legacy, str) else ''
    # Remember an explicit attribution even if a later review clears that test's tags.
    prior_link = next((key for key, item in reviews.items() if isinstance(item, dict)
                       and item.get('status') == 'issues' and legacy in _saved_tags(item.get('types'))), None)
    if legacy and legacy not in LEGACY_FEATURES and (legacy in types or prior_link):
        links = entry.get('legacy_problem_links')
        links = deepcopy(links) if isinstance(links, dict) else {}
        links[legacy] = str(result_id) if legacy in types else str(prior_link)
        entry['legacy_problem_links'] = links
    reviews[str(result_id)] = {'status': status, 'types': types, **deepcopy(context)}
    entry['problem_reviews'] = reviews
