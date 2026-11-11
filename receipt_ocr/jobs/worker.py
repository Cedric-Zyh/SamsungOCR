"""One local queue consumer per database, independent of browser requests."""
import logging
import os
from pathlib import Path
import threading

from ..runtime.progress import progress_reporting


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        handle = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b'0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class JobWorker:
    def __init__(self, store, execute, *, logger=None, dzt_service=None, dzt_transform=None):
        self.store = store
        self.execute = execute
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._start_lock = threading.Lock()
        self._thread = None
        service = dzt_service or (execute if hasattr(execute, 'source_for') else None)
        self.remote = None
        if service:
            from .danzhengtong_worker import DanzhengtongWorker
            self.remote = DanzhengtongWorker(store, service, logger=logger, transform=dzt_transform)

    def start(self):
        if self.remote:
            self.remote.start()
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='receipt-queue', daemon=True)
            self._thread.start()

    def wake(self):
        self._wake.set()
        if self.remote:
            self.remote.wake()

    def is_alive(self):
        return bool((self._thread is not None and self._thread.is_alive())
                    or (self.remote is not None and self.remote.is_alive()))

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
        if self.remote:
            self.remote.stop(timeout)

    def _run(self):
        lock = ProcessLock(self.store.database.path.with_suffix('.worker.lock'))
        try:
            while not self._stop.is_set():
                if lock.acquire():
                    break
                self._stop.wait(1)
            else:
                return
            self.store.recover()
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    job = self.store.claim()
                except Exception:
                    self.logger.exception('Queue claim failed')
                    self._stop.wait(1)
                    continue
                if job is None:
                    self._wake.wait(1)
                    continue
                result, preview, error = None, '', ''
                recognition_calls = None
                try:
                    with progress_reporting(lambda event, claimed=job: self.store.update_progress(claimed, event),
                                            paused=self.store.is_paused):
                        result, preview = self.execute(job)
                except Exception as exc:
                    self.logger.exception('Queued receipt recognition failed')
                    error = str(exc) or type(exc).__name__
                    recognition_calls = getattr(exc, 'recognition_calls', None)
                # If persistence is temporarily busy, retry the commit using
                # the computed result, rather than running OCR/API again.
                while True:
                    try:
                        if recognition_calls is None:
                            self.store.finish(job, result, preview, error)
                        else:
                            self.store.finish(job, result, preview, error,
                                              recognition_calls=recognition_calls)
                        break
                    except Exception:
                        self.logger.exception('Queue result commit failed')
                        if self._stop.wait(1):
                            return  # The next owner recovers the running item.
        except Exception:
            self.logger.exception('Background queue stopped unexpectedly')
        finally:
            lock.release()
