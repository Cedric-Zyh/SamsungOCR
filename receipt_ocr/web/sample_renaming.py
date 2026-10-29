"""Name imported samples by their recognized customer order without moving originals."""
import json
from pathlib import Path
import re

from receipt_ocr.domain.documents.pagination import page_identity
from receipt_ocr.domain.fields.schema import recognition_fields
from receipt_ocr.jobs.store import JobStore
from .sample_library import sample_number


NUMBERED_NAME = re.compile(r'[0-9]{10}(?:[-_][0-9]{2}|\s*\([0-9]+\))?')
PAGE_SUFFIX = re.compile(r'([-_][0-9]{2})$')


def recognized_sample_name(filename, result):
    stem = Path(filename).stem
    if NUMBERED_NAME.fullmatch(stem):
        return None
    number = str(recognition_fields(result).get('客户订单号') or '').strip()
    if not re.fullmatch(r'[0-9]{10}', number):
        return None
    page = PAGE_SUFFIX.search(stem)
    return number + (page.group(1) if page else '') + Path(filename).suffix


def _rename_payload(payload, original, filename):
    value = json.loads(payload)
    value.setdefault('original_filename', original)
    value['filename'] = filename
    return json.dumps(value, ensure_ascii=False)


def rename_recognized_sample(library, con, job, result):
    """Update the catalog and every sample reference in the caller's transaction."""
    original = job['filename']
    filename = recognized_sample_name(original, result)
    if not filename:
        return job, result
    entry = con.execute('SELECT * FROM sample_library WHERE filename=?', (original,)).fetchone()
    # Built-in annotated datasets keep their original, external file identities.
    if entry is None or not entry['storage_name'] or entry['deleted_at']:
        return job, result

    reserved = set(library.catalog(include_deleted=True, connection=con))
    for table in ('sample_library', 'recognition_jobs', 'results', 'latest_recognition_calls'):
        reserved.update(row[0] for row in con.execute(f'SELECT DISTINCT filename FROM {table}'))
    number = sample_number(filename)
    if any(name != original and sample_number(name) == number for name in reserved):
        JobStore._log(con, job['id'], job['task_id'], 'rename_skipped', '客户订单号已存在，保留原文件名',
                      f'{original} → {filename}', 'running')
        return job, result

    rows = con.execute('SELECT id,task_id,result_json,original_result_json FROM results WHERE filename=?',
                       (original,)).fetchall()
    con.execute('UPDATE sample_library SET filename=? WHERE filename=?', (filename, original))
    con.execute('''UPDATE recognition_jobs SET filename=?,
        stored_name=CASE WHEN stored_name=? THEN ? ELSE stored_name END WHERE filename=?''',
        (filename, f'sample:{original}', f'sample:{filename}', original))
    for row in rows:
        con.execute('''UPDATE results SET filename=?,page_key=?,result_json=?,original_result_json=?,
            stored_name=CASE WHEN stored_name=? THEN ? ELSE stored_name END WHERE id=?''',
            (filename, page_identity(filename)[0],
             _rename_payload(row['result_json'], original, filename),
             _rename_payload(row['original_result_json'], original, filename),
             f'sample:{original}', f'sample:{filename}', row['id']))
    con.execute('UPDATE latest_recognition_calls SET filename=? WHERE filename=?', (filename, original))
    con.execute('UPDATE ground_truth_history SET filename=? WHERE filename=?', (filename, original))
    for task_id in {row['task_id'] for row in rows if row['task_id']}:
        for name in (original, filename):
            library.database.reconcile_task_pages(task_id, filename=name, _connection=con)

    # UUID storage paths and artifact URLs stay stable across the public rename.
    renamed = dict(job)
    renamed.update(filename=filename, stored_name=f'sample:{filename}')
    result = {**result, 'filename': filename, 'original_filename': original}
    JobStore._log(con, job['id'], job['task_id'], 'renamed', '样单已按客户订单号重命名',
                  f'{original} → {filename}', 'running',
                  {'original_filename': original, 'filename': filename})
    return renamed, result
