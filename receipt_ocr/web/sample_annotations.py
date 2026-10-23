"""Editable sample labels, kept separate from recognition results."""
from copy import deepcopy
from datetime import date
import hashlib
import json

from receipt_ocr.domain.fields.schema import OUTPUT_FIELDS
from receipt_ocr.evaluation import GROUND_TRUTH_FIELDS
from .sample_library import LibraryConflict


def annotation_revision(entry):
    return hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class SampleAnnotations:
    def __init__(self, library):
        self.library, self.database = library, library.database

    def snapshot(self, filename):
        with self.database.connect() as con:
            con.execute('BEGIN')
            entry = self.library.catalog(include_deleted=True, connection=con).get(filename)
            history = [dict(row) for row in con.execute(
                'SELECT * FROM ground_truth_history WHERE filename=? ORDER BY id DESC LIMIT 100',
                (filename,))]
        return {'filename': filename, 'exists': entry is not None, 'entry': entry,
                'revision': annotation_revision(entry), 'history': history}

    def save(self, result_id, payload):
        if not isinstance(payload, dict):
            raise ValueError('请提供有效的样单标注')
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            filename = self.database.get_result(result_id, _connection=con)['filename']
            before = self.library.catalog(connection=con).get(filename)
            if before is None:
                raise KeyError(filename)
            if payload.get('revision') != annotation_revision(before):
                raise LibraryConflict('样单标注已在其他窗口更新，请重新打开样单后核对')
            after = self._updated(before, payload)
            if after != before:
                con.execute('''INSERT INTO sample_library(filename,entry_json) VALUES(?,?)
                    ON CONFLICT(filename) DO UPDATE SET entry_json=excluded.entry_json''',
                    (filename, json.dumps(after, ensure_ascii=False)))
                self.database.record_ground_truth_change(
                    filename=filename, result_id=result_id, before=before, after=after,
                    note=after.get('human_note', ''), action='保存样单标注与备注', _connection=con)
        return self.snapshot(filename)

    @staticmethod
    def _updated(before, payload):
        after = deepcopy(before)
        if 'fields' in payload:
            fields = payload['fields']
            allowed = set(OUTPUT_FIELDS) | set(GROUND_TRUTH_FIELDS)
            if not isinstance(fields, dict) or any(key not in allowed for key in fields):
                raise ValueError('样单标注字段无效')
            target = after.setdefault('fields', {})
            for name, value in fields.items():
                if not isinstance(value, str) or len(value) > 2000:
                    raise ValueError(f'{name}最多 2000 字')
                target[name] = value.strip()
        if 'date_present' in payload:
            present = payload['date_present']
            if present is not None and not isinstance(present, bool):
                raise ValueError('请选择日期是否填写')
            actual = payload.get('actual_date', '')
            if not isinstance(actual, str):
                raise ValueError('实际签收日期无效')
            actual = actual.strip()
            if present is True:
                try:
                    if date.fromisoformat(actual).isoformat() != actual:
                        raise ValueError()
                except ValueError:
                    raise ValueError('请填写有效的实际签收日期') from None
                after.update(date_present=True, actual_date=actual)
            elif actual:
                raise ValueError('未填写日期或暂未标注时，实际签收日期应为空')
            elif present is False:
                after.update(date_present=False, actual_date='')
            else:
                after.pop('date_present', None)
                after.pop('actual_date', None)
        if 'seal_should_match' in payload:
            match = payload['seal_should_match']
            if match is None:
                after.pop('seal_should_match', None)
            elif isinstance(match, bool):
                after['seal_should_match'] = match
            else:
                raise ValueError('请选择印章是否应匹配')
        for key, limit, label in [('human_note', 5000, '人工备注'), ('error_type', 200, '错误类型')]:
            if key in payload:
                value = payload[key]
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError(f'{label}最多 {limit} 字')
                after[key] = value.strip()
        return after
