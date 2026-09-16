"""Preserve actionable provider failures without exposing request credentials."""

import re


def safe_error_text(value, settings):
    text = str(value)
    secrets = [getattr(settings, key, '') for key in ('app_id', 'app_key', 'app_secret', 'key_id')]
    for secret in sorted((str(value) for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, '***')
    text = re.sub(r'https?://[^\s\'"<>]+', lambda match: match[0].split('?')[0] + ('?[已隐藏参数]' if '?' in match[0] else ''), text)
    text = re.sub(r'(?i)((?:authorization|appSecret|appKey|keyId|password|token|signature)\s*[=:]\s*)[^\s,;&]+', r'\1***', text)
    return ' '.join(text.split())[:1200]


def response_error_details(body):
    if not isinstance(body, dict):
        return []
    details = [f"code={body['code']}"] if body.get('code') is not None else []
    for key in ('message', 'msg', 'error', 'error_description', 'detail'):
        value = body.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            details.append(str(value))
        elif isinstance(value, dict):
            details.extend(response_error_details(value))
    return details


def provider_error(stage, settings, *, exception=None, response=None, body=None, detail=''):
    details = []
    status = getattr(response, 'status_code', None)
    if status is not None:
        details.append(f'HTTP {status}')
    if exception is not None:
        details.append(f'{type(exception).__name__}: {exception}')
    if body is None and response is not None:
        try:
            body = response.json()
        except Exception:
            # HTML/error text is displayed as plain text by the UI.
            raw = getattr(response, 'text', '')
            if isinstance(raw, str) and raw.strip():
                details.append(re.sub(r'<[^>]+>', ' ', raw))
    details.extend(response_error_details(body))
    if detail:
        details.append(detail)
    message = '；'.join(dict.fromkeys(safe_error_text(value, settings) for value in details if value))
    return RuntimeError(f'单证通{stage}失败：{message or "接口未提供错误详情"}')
