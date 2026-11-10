"""Sample queue policies and atomic batch publication."""
import json
import uuid

from receipt_ocr.jobs.service import ReceiptJobService
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.jobs.activity import active_job_filenames
from receipt_ocr.persistence.database_clock import now_iso
from .library import LibraryConflict


class SampleJobService(ReceiptJobService):
    def __init__(self, *args, library, **kwargs):
        super().__init__(*args, **kwargs)
        self.library = library

    def source_for(self, job):
        return self.library.source(job['filename'])


class SampleJobStore(JobStore):
    def __init__(self, database, library):
        super().__init__(database)
        self.library = library

    def _prepare_result(self, con, job, result):
        from receipt_ocr.samples.renaming import rename_recognized_sample
        return rename_recognized_sample(self.library, con, job, result)

    def resolve_claim_options(self, saved_options, current_settings):
        # A sample comparison keeps the exact plan selected when it was queued.
        options = super().resolve_claim_options(saved_options, current_settings)
        return {**options, **(saved_options.get("sample_options") or {})}

    def enqueue(self, names, options, settings):
        """Publish a whole batch atomically and exclude already active samples."""
        task_id, timestamp = uuid.uuid4().hex, now_iso()
        with self.database.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            catalog = self.library.catalog(connection=con)
            if any(name not in catalog for name in names):
                raise LibraryConflict('部分样单已被删除，请刷新列表')
            busy = active_job_filenames(con, names)
            accepted = [name for name in names if name not in busy]
            if not accepted:
                return {"accepted": [], "skipped": names, "task_id": None}
            con.execute(
                "INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)",
                ("recognition_defaults", json.dumps(settings, ensure_ascii=False), timestamp),
            )
            con.execute("""INSERT INTO batch_tasks
                (id,name,created_at,updated_at,status,total,ocr_backend,seal_recognition_mode,
                 recognition_config,execution_mode)
                VALUES(?,?,?,?,?,?,?,?,?,'queue')""",
                (task_id, f"样单测试 · {len(accepted)} 张", timestamp, timestamp, "处理中",
                 len(accepted), options["ocr_backend"], options["seal_recognition_mode"],
                 json.dumps(options.get("recognition_config"))))
            for index, name in enumerate(accepted):
                job_id = uuid.uuid4().hex
                job_options = dict(options)
                if options.get('_sample_execution_mode') == 'qingtong':
                    previous = con.execute('''SELECT r.result_json FROM recognition_jobs j
                        JOIN results r ON r.id=j.result_id AND r.deleted_at=''
                        WHERE j.filename=? AND j.status='succeeded' ORDER BY j.rowid DESC LIMIT 1''', (name,)).fetchone()
                    fields = (json.loads(previous['result_json']).get('fields') or {}) if previous else {}
                    job_options['previous_fields'] = {**fields, **(catalog[name].get('fields') or {})}
                con.execute("""INSERT INTO recognition_jobs
                    (id,task_id,client_key,filename,stored_name,status,start_requested,
                     payload_json,created_at,uploaded_at)
                    VALUES(?,?,?,?,?,'queued',1,?,?,?)""",
                    (job_id, task_id, str(index), name, f"sample:{name}",
                     json.dumps({**job_options, "sample_options": job_options}, ensure_ascii=False), timestamp, timestamp))
                if options.get('_sample_execution_mode') == 'danzhengtong':
                    from receipt_ocr.samples.preservation import preserve_sample_results
                    job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
                    preserve_sample_results(self, con, job)
                if options.get('_sample_execution_mode') in ('all', 'both', 'danzhengtong'):
                    job = con.execute('SELECT * FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
                    self.ensure_remote(con, job, plan=options['recognition_config'])
                self._log(con, job_id, task_id, "submitted", "开始样单测试", name,
                          "queued", event_at=timestamp)
            self.refresh_task(con, task_id)
        return {"accepted": accepted, "skipped": [name for name in names if name in busy],
                "task_id": task_id}
