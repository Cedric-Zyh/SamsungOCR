"""Seed a single-provider refresh with the sample's saved provider evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

from receipt_ocr.application.retained_providers import retained_branch
from receipt_ocr.persistence.database_clock import now_iso


_AUTOMATIC_REVIEW_ACTIONS = (
    '取回单证通结果', '补充识别页脚状态修复', '样单日期汇总更新', '样单热线印章比对更新',
)


def _has_sample_review(con, row, result):
    from receipt_ocr.domain.review_policy import has_human_edits

    return has_human_edits(result) or bool(con.execute('''SELECT 1 FROM review_history
        WHERE result_id=? AND action NOT IN (?,?,?,?) LIMIT 1''',
        (row['id'], *_AUTOMATIC_REVIEW_ACTIONS)).fetchone())


def preserve_sample_results(store, con, job):
    options = json.loads(job['payload_json'])
    rowid = con.execute('SELECT rowid FROM recognition_jobs WHERE id=?', (job['id'],)).fetchone()[0]
    history = con.execute('''SELECT r.result_json,j.branch_snapshot_json,j.payload_json FROM recognition_jobs j
        JOIN results r ON r.id=j.result_id AND r.deleted_at=''
        WHERE j.filename=? AND j.rowid<? AND j.status='succeeded' ORDER BY j.rowid DESC''',
        (job['filename'], rowid)).fetchall()
    # The caller may already have spilled a large result to disk and hold an
    # exclusive write lock. A second connection would wait on our own transaction.
    source = store.library.source(job['filename'], connection=con)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    def observations():
        for row in history:
            result = json.loads(row['result_json'])
            previous_options = json.loads(row['payload_json'])
            result.setdefault('recognition_config', previous_options.get('recognition_config') or {})
            yield result, json.loads(row['branch_snapshot_json'])

    branch = retained_branch(observations(), options['recognition_config'], digest)
    options['_provider_preservation_checked'] = True
    if branch:
        options['_preserve_provider_results'] = True
    options['sample_options'] = {**options.get('sample_options', {}),
                                '_provider_preservation_checked': True,
                                '_preserve_provider_results': bool(branch)}
    con.execute('UPDATE recognition_jobs SET payload_json=? WHERE id=?',
                (json.dumps(options, ensure_ascii=False), job['id']))
    if not branch:
        return False
    result = deepcopy(branch['base'])
    result['recognition_config'] = deepcopy(branch['snapshot']['config'])
    result['queue'] = {**result.get('queue', {}), 'job_id': job['id'], 'attempt': max(1, job['attempt'])}
    result.update(created_at=job['created_at'], updated_at=now_iso())
    preview = PurePosixPath(result.get('preview_url') or '').name
    if job['result_id']:
        result_id = job['result_id']
        store.database.result_writes.restore_retained_result(result_id, result, preview, connection=con)
    else:
        result_id = store.database.insert_result(filename=job['filename'], stored_name=job['stored_name'],
            preview_name=preview, task_id=job['task_id'], result=result, _connection=con)
    con.execute('''UPDATE recognition_jobs SET status='succeeded',result_id=?,attempt=MAX(1,attempt),
        finished_at=CASE WHEN finished_at='' THEN ? ELSE finished_at END,error_message='' WHERE id=?''',
        (result_id, now_iso(), job['id']))
    store.save_branch(con, job, result_id, branch['snapshot'], result)
    return True


def restore_previous_sample_results(store):
    """Repair the latest pre-fix remote-only run without submitting it again."""
    with store.database.connect() as con:
        jobs = con.execute('''SELECT j.id FROM recognition_jobs j
            WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
              AND j.status<>'cancelled'
              AND json_extract(j.payload_json,'$._sample_execution_mode')='danzhengtong'
              AND COALESCE(json_extract(j.payload_json,'$._provider_preservation_checked'),0)=0
              AND (j.result_id IS NULL OR EXISTS(SELECT 1 FROM results r WHERE r.id=j.result_id AND r.deleted_at=''))''').fetchall()
    # Commit each sample before moving on so history readers and workers can
    # access the database throughout a large restoration.
    for candidate in jobs:
        with store.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            # Another request may have restored, cancelled or superseded this job
            # since the initial scan. Recheck after acquiring the write lock.
            job = con.execute('''SELECT * FROM recognition_jobs j WHERE j.id=?
                AND NOT EXISTS(SELECT 1 FROM recognition_jobs newer
                    WHERE newer.filename=j.filename AND newer.rowid>j.rowid)
                AND j.status<>'cancelled'
                AND json_extract(j.payload_json,'$._sample_execution_mode')='danzhengtong'
                AND COALESCE(json_extract(j.payload_json,'$._provider_preservation_checked'),0)=0
                AND (j.result_id IS NULL OR EXISTS(SELECT 1 FROM results r WHERE r.id=j.result_id AND r.deleted_at=''))''',
                (candidate['id'],)).fetchone()
            if job is None:
                continue
            try:
                preserved = preserve_sample_results(store, con, job)
            except (KeyError, FileNotFoundError):
                continue  # Deleted sample sources must never be resurrected.
            if preserved:
                store.refresh_task(con, job['task_id'])


def repair_preserved_sample_footers(store):
    """Correct already-applied footer misreports using saved evidence only."""
    from receipt_ocr.application.saved_footer import saved_footer_evidence
    from receipt_ocr.domain.decision import finalize_result

    candidates = '''FROM recognition_jobs j JOIN results r ON r.id=j.result_id
        JOIN danzhengtong_jobs d ON d.job_id=j.id AND d.active=1
        WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
          AND j.status='succeeded' AND d.status='applied' AND d.lease_until=''
          AND json_extract(j.payload_json,'$._preserve_provider_results')=1
          AND r.deleted_at='' AND r.review_status NOT IN ('确认通过','确认不通过')
          AND r.human_note='' AND r.error_type=''
          AND json_extract(r.result_json,'$.document_type.type')='receipt'
          AND EXISTS(SELECT 1 FROM json_each(r.result_json,'$.review_issues') i
              WHERE json_extract(i.value,'$.code')='footer_unconfirmed')'''

    def correct(result, footer):
        if ((result.get('document_type') or {}).get('type') != 'receipt'
                or (result.get('signature_footer') or {}).get('present') is False):
            return result
        issues = result.get('review_issues') or []
        removed = [item for item in issues if item.get('code') == 'footer_unconfirmed']
        if not removed:
            return result
        result['signature_footer'] = deepcopy(footer)
        result['review_issues'] = [item for item in issues if item not in removed]
        messages = {item['message'] for item in removed}
        result['review_reasons'] = [text for text in result.get('review_reasons') or [] if text not in messages]
        return finalize_result(result)

    with store.database.connect() as con:
        jobs = con.execute('SELECT j.id ' + candidates).fetchall()
    repaired = 0
    for candidate in jobs:
        with store.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT r.*,j.task_id job_task_id,j.branch_snapshot_json ' + candidates + ' AND j.id=?',
                              (candidate['id'],)).fetchone()
            if row is None:
                continue
            result = json.loads(row['result_json'])
            if _has_sample_review(con, row, result):
                continue
            branch = json.loads(row['branch_snapshot_json'])
            snapshot = branch.get('snapshot') or {}
            footer = saved_footer_evidence(result, snapshot.get('context') or {},
                                           snapshot.get('evidence') or {}, branch.get('base') or {})
            if footer.get('present') is not True:
                continue
            result = correct(result, footer)
            original = correct(json.loads(row['original_result_json']), footer)
            store.database.result_writes.repair_saved_result(row['id'], result=result,
                review_status=result['review_status'], final_result=result['final_result'],
                action='补充识别页脚状态修复', original_result=original, connection=con)
            if snapshot:
                snapshot.setdefault('context', {}).update(has_footer=True, footer_detection=deepcopy(footer))
            if branch.get('base') is not None:
                branch['base']['signature_footer'] = deepcopy(footer)
            con.execute('UPDATE recognition_jobs SET branch_snapshot_json=? WHERE id=?',
                        (json.dumps(branch, ensure_ascii=False), candidate['id']))
            store.refresh_task(con, row['job_task_id'])
            repaired += 1
    return repaired


def refresh_sample_date_choices(store):
    """Update saved date summaries while preserving provider evidence and verdicts."""
    from receipt_ocr.domain.date_selection import refresh_date_selection

    candidates = '''FROM recognition_jobs j JOIN results r ON r.id=j.result_id
        JOIN danzhengtong_jobs d ON d.job_id=j.id AND d.active=1
        WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
          AND j.status='succeeded' AND d.status='applied' AND d.lease_until=''
          AND r.deleted_at='' AND r.review_status NOT IN ('确认通过','确认不通过')
          AND r.human_note='' AND r.error_type=''
          AND json_array_length(r.result_json,'$.recognition_variants.date')>1'''
    with store.database.connect() as con:
        jobs = con.execute('SELECT j.id ' + candidates).fetchall()
    changed = 0
    for candidate in jobs:
        with store.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT r.*,j.task_id job_task_id ' + candidates + ' AND j.id=?',
                              (candidate['id'],)).fetchone()
            if row is None:
                continue
            current = json.loads(row['result_json'])
            if _has_sample_review(con, row, current):
                continue
            result = refresh_date_selection(current)
            if result == current:
                continue
            store.database.result_writes.repair_saved_result(row['id'], result=result,
                review_status=row['review_status'], final_result=row['final_result'],
                action='样单日期汇总更新', connection=con)
            store.refresh_task(con, row['job_task_id'])
            changed += 1
    return changed


def refresh_sample_seal_contacts(store):
    """Apply the contact-field comparison fix to completed remote sample results."""
    from receipt_ocr.domain.seal_contact_refresh import refresh_seal_contact_results

    candidates = '''FROM recognition_jobs j JOIN results r ON r.id=j.result_id
        JOIN danzhengtong_jobs d ON d.job_id=j.id AND d.active=1
        WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
          AND j.status='succeeded' AND d.status='applied' AND d.lease_until=''
          AND r.deleted_at='' AND r.review_status NOT IN ('确认通过','确认不通过')
          AND r.human_note='' AND r.error_type=''
          AND EXISTS(SELECT 1 FROM json_each(r.result_json,'$.recognition_variants.seal') v
              WHERE json_extract(v.value,'$.method')='danzhengtong'
                AND json_extract(v.value,'$.details.seal_check.recognized') LIKE '%维修热线%')'''
    with store.database.connect() as con:
        jobs = con.execute('SELECT j.id ' + candidates).fetchall()
    changed = 0
    for candidate in jobs:
        with store.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT r.*,j.task_id job_task_id ' + candidates + ' AND j.id=?',
                              (candidate['id'],)).fetchone()
            if row is None:
                continue
            current = json.loads(row['result_json'])
            if _has_sample_review(con, row, current):
                continue
            result = refresh_seal_contact_results(current)
            if result == current:
                continue
            store.database.result_writes.repair_saved_result(row['id'], result=result,
                review_status=result['review_status'], final_result=result['final_result'],
                action='样单热线印章比对更新', connection=con)
            store.refresh_task(con, row['job_task_id'])
            changed += 1
    return changed
