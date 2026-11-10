"""Durable human company markers in the sample-only database."""
import hashlib
import json

from receipt_ocr.persistence.database import now_iso


STATES = {'unmarked', 'pending', 'checked', 'issue'}


class MarkConflict(ValueError):
    pass


class SampleCompanyMarks:
    def __init__(self, database):
        self.database = database
        with database.connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS sample_company_marks (
                company TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                reviewed_version TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )''')

    def snapshot(self, catalog, con):
        companies = {name: str((entry.get('fields') or {}).get('客户名称') or '').strip() or '未分类'
                     for name, entry in catalog.items()}
        evidence = {company: [] for company in companies.values()}
        # Keep the previous completed observation while a rerun is queued.
        # A completed failure is also a new outcome worth checking.
        for row in con.execute('''SELECT id,filename,result_id FROM recognition_jobs
                WHERE rowid IN (SELECT MAX(rowid) FROM recognition_jobs
                    WHERE status IN ('succeeded','failed') GROUP BY filename)'''):
            if row['filename'] in companies:
                evidence[companies[row['filename']]].append((row['filename'], row['id'], row['result_id']))
        versions = {company: hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode()).hexdigest()
                    for company, rows in evidence.items()}
        marks = {}
        for row in con.execute('SELECT * FROM sample_company_marks'):
            if row['company'] in versions:
                mark = dict(row)
                mark['has_new_results'] = mark['state'] == 'checked' and mark['reviewed_version'] != versions[row['company']]
                marks[row['company']] = mark
        return marks, versions

    def save(self, catalog, payload):
        if not isinstance(payload, dict):
            raise ValueError('请填写公司标记')
        company, state, note = payload.get('company'), payload.get('state'), payload.get('note')
        revision, reviewed = payload.get('revision'), payload.get('reviewed', False)
        if not isinstance(company, str) or not company:
            raise ValueError('请选择客户公司')
        if not isinstance(state, str) or state not in STATES:
            raise ValueError('请选择有效的人工标记')
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('备注最多填写 500 个字')
        if type(revision) is not int or revision < 0 or not isinstance(reviewed, bool):
            raise ValueError('标记信息无效，请重新打开后保存')
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            marks, versions = self.snapshot(catalog, con)
            if company not in versions:
                raise KeyError(company)
            previous = marks.get(company, {})
            if revision != previous.get('revision', 0):
                raise MarkConflict('这家公司的标记已被修改，请关闭后重新打开；当前输入已保留')
            if state == 'checked' and previous.get('state') != 'checked' and not reviewed:
                raise ValueError('请确认已检查当前结果')
            if state == 'checked' and reviewed and payload.get('result_version') != versions[company]:
                raise MarkConflict('这家公司刚有新的测试结果，请查看后重新标记；当前输入已保留')
            baseline = versions[company] if state == 'checked' and reviewed else previous.get('reviewed_version', '')
            con.execute('''INSERT INTO sample_company_marks
                (company,state,note,reviewed_version,revision,updated_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(company) DO UPDATE SET state=excluded.state,note=excluded.note,
                    reviewed_version=excluded.reviewed_version,revision=excluded.revision,updated_at=excluded.updated_at''',
                (company, state, note.strip(), baseline, revision + 1, now_iso()))
            updated, _ = self.snapshot(catalog, con)
            return updated[company]
