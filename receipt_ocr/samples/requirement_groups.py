"""Persist per-company requirement groups separately from human annotations."""
import json

from receipt_ocr.domain.seal_standards import requirement_key


def group_name(entry):
    return str(entry.get('requirement_group') or '').strip()


def _company(entry):
    return str((entry.get('fields') or {}).get('客户名称') or '').strip() or '未分类'


def _recognized_text(value, maximum):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if (not value or len(value) > maximum or any(ord(char) < 32 for char in value)
            or value in {'未识别', '未归类', '未分类', '无', '无要求'}):
        return ''
    return value


def classify_sample_groups(library, *, company='', requirement=None, filenames=None):
    """Classify a snapshot of the chosen scope using only its latest completed jobs."""
    if not isinstance(company, str) or len(company.strip()) > 200:
        raise ValueError('客户公司无效')
    if requirement is not None and not isinstance(requirement, str):
        raise ValueError('签章要求无效')
    if filenames is not None and (not isinstance(filenames, list) or not 1 <= len(filenames) <= 5000
                                  or any(not isinstance(name, str) for name in filenames)):
        raise ValueError('请选择要归类的样单')
    company = company.strip()
    classified, created_companies, created_groups = [], set(), []
    processed = 0
    with library.database.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        catalog = library.catalog(connection=con)
        companies = {_company(entry) for entry in catalog.values()} - {'未分类'}
        if company and company != '未分类' and company not in companies:
            raise KeyError(company)
        if filenames is not None and any(name not in catalog for name in filenames):
            raise ValueError('部分样单不存在，请刷新列表')
        names = set(filenames) if filenames is not None else None
        entries = {name: entry for name, entry in catalog.items()
                   if (not company or _company(entry) == company)
                   and (requirement is None or requirement_key(group_name(entry)) == requirement)
                   and (names is None or name in names)}
        groups = {(_company(entry), requirement_key(group_name(entry))): group_name(entry)
                  for entry in catalog.values() if requirement_key(group_name(entry))}
        busy = {row[0] for row in con.execute(
            "SELECT filename FROM recognition_jobs WHERE status IN ('awaiting_upload','queued','running')")}
        recognized = {row['filename']: json.loads(row['fields'] or '{}') for row in con.execute('''
            SELECT j.filename, json_extract(r.result_json, '$.fields') AS fields
            FROM recognition_jobs j JOIN results r ON r.id=j.result_id
            WHERE j.status='succeeded' AND r.deleted_at='' AND j.rowid IN (
                SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
        ''')}
        for name, entry in entries.items():
            fields = recognized.get(name)
            if name in busy or not isinstance(fields, dict):
                continue
            processed += 1
            target_company = _company(entry)
            if target_company == '未分类':
                target_company = _recognized_text(fields.get('客户名称'), 200)
            if not target_company:
                continue
            value = (_recognized_text(fields.get('签章要求'), 1000)
                     if '签章要求' in fields else group_name(entry))
            key = requirement_key(value)
            identity = (target_company, key)
            if key and identity not in groups:
                groups[identity] = value
                created_groups.append(value)
            updated = {**entry, 'fields': {**(entry.get('fields') or {}), '客户名称': target_company},
                       'requirement_group': groups[identity] if key else ''}
            # Do not materialize empty metadata for unchanged, still ungrouped records.
            if _company(entry) == target_company and group_name(entry) == updated['requirement_group']:
                continue
            con.execute('''INSERT INTO sample_library(filename,entry_json) VALUES(?,?)
                ON CONFLICT(filename) DO UPDATE SET entry_json=excluded.entry_json''',
                (name, json.dumps(updated, ensure_ascii=False)))
            catalog[name] = updated
            classified.append({'filename': name, 'company': target_company, 'requirement': updated['requirement_group']})
            if target_company not in companies:
                created_companies.add(target_company)
                companies.add(target_company)
        remaining = sum(_company(catalog[name]) == '未分类' or not requirement_key(group_name(catalog[name]))
                        for name in entries)
    return {'classified': classified, 'created_companies': sorted(created_companies),
            'created_groups': created_groups, 'remaining': remaining, 'company': company,
            'processed': processed, 'skipped': len(entries) - processed}


def classify_requirements(library, company, *, requirement=None, filenames=None):
    if not isinstance(company, str) or not company.strip():
        raise ValueError('请选择客户公司')
    return classify_sample_groups(library, company=company, requirement=requirement, filenames=filenames)
