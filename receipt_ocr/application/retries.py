"""Shared retry preparation and execution, independent of HTTP responses."""
from dataclasses import dataclass
import uuid

from .options import resolve_recognition_options
from ..domain.fields.schema import recognition_fields


class MissingRetrySource(FileNotFoundError):
    def __init__(self, filename):
        super().__init__('原始图片不存在')
        self.filename_label = filename


class InvalidRetrySelection(ValueError):
    pass


@dataclass(frozen=True)
class PreparedRetry:
    current: dict
    source: object
    options: object


@dataclass
class ResultRetryService:
    database: object
    documents: object
    source_for_record: object
    api_enabled: bool
    jobs: object
    data_dir: object

    def prepare(self, result_id, payload, *, background=False):
        current = self.database.get_result(result_id)
        source = self.source_for_record(current)
        if source is None or not source.is_file():
            raise MissingRetrySource(current['filename'])
        # Preserve the existing queue default for legacy records; synchronous
        # retries can also recover the mode recorded in their old seal check.
        fallback = 'local' if background else (current.get('seal_check') or {}).get('recognition_mode')
        options = resolve_recognition_options(
            settings=self.database.get_json_setting('recognition_defaults'),
            recognition_config=payload.get('recognition_config', current.get('recognition_config')),
            ocr_backend=payload.get('ocr_backend') or current.get('ocr_backend'),
            seal_recognition_mode=payload.get('seal_recognition_mode') or current.get('seal_recognition_mode') or fallback,
            api_enabled=self.api_enabled,
        )
        return PreparedRetry(current, source, options)

    def execute(self, result_id, prepared):
        current, options = prepared.current, prepared.options
        result, preview_name = self.documents.recognize(
            prepared.source, recognition_config=options.recognition_config,
            previous_fields=recognition_fields(current), filename=current['filename'],
            created_at=current['created_at'], ocr_backend=options.ocr_backend,
            seal_recognition_mode=options.seal_recognition_mode,
        )
        return self.database.replace_after_retry(result_id, result, preview_name)

    def enqueue(self, ids, payload):
        if (not isinstance(ids, list) or not ids or len(ids) > 2000
                or any(type(i) is not int or i <= 0 for i in ids)):
            raise InvalidRetrySelection('请选择有效的回单记录')
        ids = list(dict.fromkeys(ids))
        options = {}
        for result_id in ids:
            prepared = self.prepare(result_id, payload, background=True)
            source, current = prepared.source, prepared.current
            options[result_id] = {
                **prepared.options.as_dict(),
                '_stored_name': f'sample:{source.name}' if source.parent.resolve() == self.data_dir.resolve() else current['stored_name'],
            }
        # Validate every receipt before admitting any job; the store writes the
        # complete batch atomically and rechecks revisions in its transaction.
        return self.jobs.create_retries(uuid.uuid4().hex, ids, options)
