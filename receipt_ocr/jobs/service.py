"""Execute a persisted image job without a Flask request context."""
from datetime import datetime
import json

from ..persistence.database import now_iso
from ..application.document_service import DocumentRecognitionService


class ReceiptJobService:
    def __init__(self, analyze, *, data_dir, upload_dir, preview_dir, artifact_dir):
        self.data_dir = data_dir
        self.upload_dir = upload_dir
        self.documents = DocumentRecognitionService(analyze, preview_dir, artifact_dir, now_iso)

    def __call__(self, job):
        options = json.loads(job['payload_json'])
        stored = job['stored_name']
        if stored.startswith('sample:'):
            source = self.data_dir / stored.split(':', 1)[1]
        else:
            source = self.upload_dir / stored
        result, preview = self.documents.recognize(source,
            recognition_config=options.get('recognition_config'), previous_fields=options.get('previous_fields'),
            filename=job['filename'], created_at=job['created_at'],
            ocr_backend=options.get('ocr_backend'), seal_recognition_mode=options.get('seal_recognition_mode'))
        result['queue'] = {'job_id': job['id'], 'attempt': job['attempt'],
                           'uploaded_at': job['uploaded_at'], 'started_at': job['started_at'],
                           'wait_seconds': max(0, (datetime.fromisoformat(job['started_at']) -
                                                  datetime.fromisoformat(job['uploaded_at'])).total_seconds())}
        return result, preview
