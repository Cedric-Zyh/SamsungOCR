"""Execute a persisted image job without a Flask request context."""
from datetime import datetime
import json
import uuid

from .database import now_iso


class ReceiptJobService:
    def __init__(self, analyze, *, data_dir, upload_dir, preview_dir, artifact_dir):
        self.analyze = analyze
        self.data_dir = data_dir
        self.upload_dir = upload_dir
        self.preview_dir = preview_dir
        self.artifact_dir = artifact_dir

    def __call__(self, job):
        options = json.loads(job['payload_json'])
        stored = job['stored_name']
        if stored.startswith('sample:'):
            source = self.data_dir / stored.split(':', 1)[1]
        else:
            source = self.upload_dir / stored
        if not source.is_file():
            raise FileNotFoundError('原始图片不存在，请补充图片后重新识别')
        token = uuid.uuid4().hex
        preview = f'{token}.jpg'
        result = self.analyze(source, self.preview_dir / preview,
            artifact_dir=self.artifact_dir / token, artifact_url_prefix=f'/files/artifacts/{token}',
            recognition_config=options.get('recognition_config'), previous_fields=options.get('previous_fields'),
            filename=job['filename'],
            ocr_backend=options.get('ocr_backend'), seal_recognition_mode=options.get('seal_recognition_mode'))
        result.update(filename=job['filename'], preview_url=f'/files/previews/{preview}',
                      created_at=job['created_at'], updated_at=now_iso())
        result['queue'] = {'job_id': job['id'], 'attempt': job['attempt'],
                           'uploaded_at': job['uploaded_at'], 'started_at': job['started_at'],
                           'wait_seconds': max(0, (datetime.fromisoformat(job['started_at']) -
                                                  datetime.fromisoformat(job['uploaded_at'])).total_seconds())}
        return result, preview
