"""Own the production application's resources and startup/shutdown sequence.

Construction does not access the database or start workers. A runtime has one
initialization and one shutdown; a different configuration gets a new owner.
"""
from threading import RLock
from dataclasses import replace
import time

from ..application.analyzer import ReceiptAnalyzer
from ..application import RecognitionService
from ..application.configured_recognition import ConfiguredRecognition
from ..evaluation import load_ground_truth
from ..jobs.service import ReceiptJobService
from ..jobs.store import JobStore
from ..jobs.worker import JobWorker
from ..persistence.database import Database
from ..persistence.seal_standard_images import migrate_standard_images
from ..recognition.seal.reference.matcher import SealReferenceMatcher
from .maintenance import StorageMaintenance
from .remote_settings import restore


class ApplicationRuntime:
    def __init__(self, paths, *, database=None, analyzer=None, matcher=None,
                 recognition=None, job_store=None, logger=None, worker_factory=JobWorker):
        if database is not None:
            paths = replace(paths, database_path=database.path)
        self.paths = paths
        self.database = database if database is not None else Database(paths.database_path)
        self.analyzer = analyzer if analyzer is not None else ReceiptAnalyzer()
        self.matcher = matcher if matcher is not None else SealReferenceMatcher(paths.artifact_dir)
        self.recognition = recognition if recognition is not None else RecognitionService(self.analyzer, self.matcher)
        self.configured_recognition = ConfiguredRecognition(self.database, self.analyzer, self.recognition, restore)
        if job_store is not None and job_store.database is not self.database:
            raise ValueError('任务队列必须使用运行环境的数据库')
        self.job_store = job_store if job_store is not None else JobStore(self.database)
        self.job_worker = None
        self.logger = logger
        self._worker_factory = worker_factory
        self.maintenance = StorageMaintenance(self.database, upload_dir=paths.upload_dir,
            preview_dir=paths.preview_dir, artifact_dir=paths.artifact_dir,
            export_dir=paths.export_dir, logger=logger)
        self._lock = RLock()
        self._last_cleanup = 0.0
        self.initialized = False
        self.closed = False
        self.stopping = False

    def initialize(self, *, start_worker=True):
        with self._lock:
            if self.closed:
                raise RuntimeError('运行环境已关闭，请创建新的运行环境')
            if self.stopping:
                raise RuntimeError('后台任务正在停止，退出完成前不能重新初始化')
            if not self.initialized:
                paths = self.paths
                for directory in (paths.upload_dir, paths.preview_dir, paths.artifact_dir, paths.export_dir):
                    directory.mkdir(parents=True, exist_ok=True)
                self.database.initialize()
                migrate_standard_images(self.database,
                    history_databases=(self.database.path.parent / 'sample-center' / 'results.db',))
                self.configured_recognition.restore_credentials()
                self.job_store.initialize()
                self.maintenance.purge_expired()
                self._last_cleanup = time.monotonic()
                self.database.recover_interrupted_tasks()
                self.database.enforce_uncertain_review_queue()
                self.matcher.refresh(self.database, load_ground_truth(paths.ground_truth_path))
                self.initialized = True
            else:
                self.configured_recognition.restore_credentials()
            if start_worker and self.job_worker is None:
                paths = self.paths
                service = ReceiptJobService(self.configured_recognition.recognize,
                    data_dir=paths.data_dir, upload_dir=paths.upload_dir,
                    preview_dir=paths.preview_dir, artifact_dir=paths.artifact_dir)
                worker = self._worker_factory(self.job_store, service, logger=self.logger)
                self.job_worker = worker
                try:
                    worker.start()
                except BaseException:
                    self.stopping = True
                    worker.stop()
                    if not worker.is_alive():
                        self.job_worker = None
                        self.stopping = False
                    raise

    def maybe_purge(self):
        with self._lock:
            if self.closed or self.stopping or not self.initialized or time.monotonic() - self._last_cleanup < 3600:
                return
            self.maintenance.purge_expired()
            self._last_cleanup = time.monotonic()

    def close(self):
        with self._lock:
            if self.closed:
                return True
            self.stopping = True
            if self.job_worker is not None:
                self.job_worker.stop()
                if self.job_worker.is_alive():
                    return False  # Keep ownership until all in-flight work exits.
            self.closed = True
            return True
