"""Sample business operations and owned runtime resources."""
from receipt_ocr.api.revisions import with_review_revision
from receipt_ocr.domain.fields.schema import derive_signature_check
from receipt_ocr.domain.seal_standards import apply_standard_check
from receipt_ocr.jobs.worker import JobWorker
from receipt_ocr.persistence.database import Database
from .dependencies import SampleDependencies
from .jobs import SampleJobService, SampleJobStore
from .library import SampleLibrary
from .listing import SampleListing
from .thumbnails import SampleThumbnails
from .annotations import SampleAnnotations
from .company_marks import SampleCompanyMarks
from .execution import sample_execution_options
from .result_filters import project_sample_seal_result
from .urls import sample_urls


class SampleNotFound(KeyError):
    """The requested sample source or saved result no longer exists."""


class SampleCenter:
    def __init__(self, dependencies: SampleDependencies):
        self.dependencies = dependencies
        self.database = Database(dependencies.database.path.parent / "sample-center" / "results.db")
        self.database.path.parent.mkdir(parents=True, exist_ok=True)
        self.database.initialize()
        self.library = SampleLibrary(dependencies.paths, self.database)
        self.thumbnails = SampleThumbnails(self.database.path.parent / 'thumbnails')
        self.annotations = SampleAnnotations(self.library)
        self.store = SampleJobStore(self.database, self.library)
        self.store.initialize()
        from receipt_ocr.samples.preservation import (
            restore_previous_sample_results, repair_preserved_sample_footers,
            refresh_sample_date_choices, refresh_sample_seal_contacts,
        )
        restore_previous_sample_results(self.store)
        repair_preserved_sample_footers(self.store)
        refresh_sample_date_choices(self.store)
        refresh_sample_seal_contacts(self.store)
        self.company_marks = SampleCompanyMarks(self.database)
        self.preview_dir = self.database.path.parent / "previews"
        self.artifact_dir = self.database.path.parent / "artifacts"
        self.preview_dir.mkdir(exist_ok=True)
        self.artifact_dir.mkdir(exist_ok=True)
        service = SampleJobService(
            dependencies.analyze, data_dir=dependencies.paths.data_dir, upload_dir=self.library.directory,
            preview_dir=self.preview_dir, artifact_dir=self.artifact_dir, library=self.library)

        def execute(job):
            result, preview = service(job)
            return sample_urls(result), preview

        self.worker = JobWorker(self.store, execute, logger=dependencies.logger, dzt_service=service, dzt_transform=sample_urls)
        self._listing = SampleListing(self.database, self.library, self.company_marks, dependencies)

    def project_result(self, item, *, _connection=None):
        """Compare sample evidence against the shared standards, never production result IDs."""
        item["signature_check"] = derive_signature_check(item.get("fields") or {}, item.get("signature_check"))
        # Failed recognition still has a source image available for inspection.
        if not item.get("preview_url"):
            from urllib.parse import quote
            item["preview_url"] = f"/files/samples/original/{quote(item['filename'])}"
        item["sample_center"] = True
        requirement = (item.get("fields") or {}).get("签章要求", "")
        standard = self.dependencies.database.get_seal_standard(requirement, _connection=_connection)
        return with_review_revision(project_sample_seal_result(apply_standard_check(item, standard)))

    def result(self, result_id):
        try:
            item = self.database.get_result(result_id)
        except KeyError as exc:
            raise SampleNotFound(result_id) from exc
        return self.project_result(item)

    def catalog(self):
        return self.library.catalog()

    def source(self, name, *, include_deleted=False):
        try:
            return self.library.source(name, include_deleted=include_deleted)
        except KeyError as exc:
            raise SampleNotFound(name) from exc

    def listing(self, revision=None):
        return self._listing.listing(revision)

    def run(self, names, mode=None, *, settings, api_enabled):
        if (not isinstance(names, list) or not 1 <= len(names) <= 5000
                or any(not isinstance(name, str) for name in names)):
            raise ValueError("请选择要运行的样单")
        names = list(dict.fromkeys(names))
        catalog = self.catalog()
        if any(name not in catalog for name in names):
            raise ValueError("部分样单不存在，请刷新列表后重试")
        for name in names:
            self.source(name)
        options = sample_execution_options(settings, mode, api_enabled=api_enabled)
        return self.store.enqueue(names, options, settings)

    def history(self):
        with self.database.connect() as con:
            rows = con.execute("""SELECT j.filename,j.status,j.created_at,j.finished_at,
                j.error_message,j.result_id,
                COALESCE(json_extract(j.payload_json,'$._sample_execution_mode'),'') AS execution_mode
                FROM recognition_jobs j ORDER BY j.rowid DESC LIMIT 200""").fetchall()
        return [dict(row) for row in rows]

    def review_history(self, result_id):
        name = self.result(result_id)['filename']
        rows = self.database.history(result_id) + self.database.ground_truth_history(name)
        return sorted(rows, key=lambda row: (row['changed_at'], row['id']), reverse=True)

    def close(self):
        self.worker.stop()
