"""Read-only terms, actual correction policy, and saved per-stamp history."""
import json
from pathlib import Path
import sqlite3

from flask import abort, jsonify

from receipt_ocr.domain.seal_standards import requirement_key
from receipt_ocr.recognition.seal.postprocess.name_correction import (
    MAX_NAME_EDITS, MAX_BRANCH_EDITS, MAX_EDGE_NOISE,
    company_name_terms,
)


def _history(path, standard, name, source_kind):
    if not path.is_file() or not name:
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function('seal_key', 1, requirement_key, deterministic=True)
    try:
        rows = connection.execute('''
            SELECT r.id, r.filename, r.created_at,
                json_extract(a.value,'$.index') AS stamp_index, e.value AS correction
            FROM results r, json_each(r.result_json,'$.processing_artifacts.seals') a,
                json_each(a.value,'$.name_corrections') e
            WHERE r.deleted_at='' AND json_extract(e.value,'$.corrected_text')=?
                AND seal_key(COALESCE(
                    json_extract(r.summary_json,'$.page_review_override.fields.签章要求'),
                    json_extract(r.result_json,'$.fields.签章要求')))=?
            ORDER BY r.id DESC LIMIT 21
        ''', (name, requirement_key(standard['requirement']))).fetchall()
    except sqlite3.OperationalError as error:
        # A missing sample database/table means this installation has no
        # history there. Other failures must be visible to the caller.
        if 'no such table' not in str(error):
            raise
        rows = []
    finally:
        connection.close()
    return [{'result_id': row['id'], 'filename': row['filename'], 'created_at': row['created_at'],
             'source_kind': source_kind, 'stamp_index': row['stamp_index'],
             **json.loads(row['correction'])} for row in rows]


def standard_recognition_info(ctx, standard_id):
    standards = ctx.database.list_seal_standards()
    standard = next((item for item in standards if item['id'] == standard_id), None)
    if standard is None:
        abort(404)
    terms = company_name_terms(standard['requirement'])
    siblings = [company_name_terms(item['requirement']) for item in standards]
    branches = sorted({item['branch'] for item in siblings
                       if terms['company'] and item['company'] == terms['company'] and item['branch']})
    correction_state = ('已启用' if terms['correction_supported'] else
                        '名称过短，保留原文' if terms['parse_status'] == 'parsed' else '待确认拆分')
    rules = [
        {'key': 'company_name', 'title': '名称近似校正', 'state': correction_state,
         'description': f'名称总计最多 {MAX_NAME_EDITS} 处字差，其中分支最多 {MAX_BRANCH_EDITS} 处；高相似且候选唯一时校正'},
        {'key': 'edge_noise', 'title': '首尾杂字', 'state': correction_state,
         'description': f'首尾各最多 {MAX_EDGE_NOISE} 个字，包含末尾重复字；按完整名称核对'},
        {'key': 'number', 'title': '章内编号', 'state': '独立比对',
         'description': '章号、站点和授权代码、仓库代码独立核对，名称纠错不替换这些信息'},
    ]
    name = terms['company'] + terms['branch']
    database_path = Path(ctx.database.path)
    history = []
    for path, kind in ((database_path, 'receipt'),
                       (database_path.parent / 'sample-center' / 'results.db', 'sample')):
        history.extend(_history(path, standard, name, kind))
    history.sort(key=lambda item: (item.get('created_at') or '', item['result_id']), reverse=True)
    return jsonify(standard_id=standard_id, revision=standard['revision'], terms=terms,
                   branches=branches, rules=rules, history=history[:20],
                   has_more=len(history) > 20)
