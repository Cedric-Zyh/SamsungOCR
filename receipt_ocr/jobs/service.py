"""Execute a persisted image job without a Flask request context."""
from datetime import datetime
import hashlib
import json

from ..persistence.database import now_iso
from ..application.document_service import DocumentRecognitionService


class ReceiptJobService:
    def __init__(self, analyze, *, data_dir, upload_dir, preview_dir, artifact_dir):
        self.data_dir = data_dir
        self.upload_dir = upload_dir
        self.documents = DocumentRecognitionService(analyze, preview_dir, artifact_dir, now_iso)

    def source_for(self, job):
        stored = job['stored_name']
        if stored.startswith('sample:'):
            return self.data_dir / stored.split(':', 1)[1]
        return self.upload_dir / stored

    def __call__(self, job):
        from copy import deepcopy
        from .danzhengtong_store import scope_plan, has_remote
        from ..application.deferred_document import empty_snapshot, combine_saved, mark_danzhengtong_pending
        options = json.loads(job['payload_json'])
        source = self.source_for(job)
        plan = scope_plan(options)
        independent = options.get('_independent_dzt')
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        scope = options.get('_execution_scope', 'all')
        include_remote = scope != 'local' or job.get('_has_dzt_task')
        if job.get('_has_dzt_task'):
            remote_plan = job.get('_dzt_plan') or plan
            for stage in ('fields', 'handwriting', 'date', 'seal'):
                if 'danzhengtong' in remote_plan.get(stage, []):
                    plan[stage] = list(dict.fromkeys([*plan[stage], 'danzhengtong']))
        regular = deepcopy(plan)
        if independent:
            for stage, methods in regular.items():
                if isinstance(methods, list):
                    regular[stage] = [] if scope == 'danzhengtong' else [method for method in methods if method != 'danzhengtong']
        has_regular = any(regular.get(stage) for stage in ('fields', 'handwriting', 'products', 'date', 'seal'))
        if independent and not has_regular:
            effective = deepcopy(plan)
            if scope == 'danzhengtong':
                for stage, methods in effective.items():
                    if isinstance(methods, list):
                        effective[stage] = [method for method in methods if method == 'danzhengtong']
            snapshot = empty_snapshot(effective, options.get('previous_fields'))
            result = combine_saved(source, job['filename'], snapshot, pending=include_remote and has_remote(plan))
            result.update(filename=job['filename'], created_at=job['created_at'], updated_at=now_iso(), processing_seconds=0)
            preview = ''
        else:
            kwargs = {'capture_assembly': True}
            result, preview = self.documents.recognize(source,
                recognition_config=regular if independent else options.get('recognition_config'), previous_fields=options.get('previous_fields'),
                filename=job['filename'], created_at=job['created_at'],
                ocr_backend=options.get('ocr_backend'), seal_recognition_mode=options.get('seal_recognition_mode'), **kwargs)
            snapshot = result.pop('_assembly_snapshot', None)
            if independent and snapshot:
                snapshot['config'] = plan if include_remote else regular
                if include_remote and has_remote(plan):
                    result = mark_danzhengtong_pending(result, plan)
        if snapshot:
            snapshot['source_hash'] = source_hash
            result['_assembly_snapshot'] = snapshot
        result['queue'] = {'job_id': job['id'], 'attempt': job['attempt'],
                           'uploaded_at': job['uploaded_at'], 'started_at': job['started_at'],
                           'wait_seconds': max(0, (datetime.fromisoformat(job['started_at']) -
                                                  datetime.fromisoformat(job['uploaded_at'])).total_seconds())}
        return result, preview
