"""Skip local stamp processing when its printed requirement is absent."""


def has_seal_requirement(fields):
    return bool(str((fields or {}).get('签章要求') or '').strip())


def no_requirement_seal_check():
    return {
        'requirement': '', 'recognized': '', 'all_recognized': [],
        'status': '缺少比对依据', 'reliable': False, 'score': 0.0, 'confidence': 0.0,
        'execution_status': 'skipped', 'skip_reason': 'missing_requirement',
        'message': '未提供签章要求，已跳过本地印章识别',
        'backend': '未执行（未提供签章要求）', 'recognition_mode': 'local',
        'regions': [], 'api': {'enabled': False, 'requested': False,
                               'message': '未提供签章要求，已跳过本地印章处理'},
    }
