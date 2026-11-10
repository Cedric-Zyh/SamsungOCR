"""Bounded conditional reads for the sample directory and its list projection."""
from pathlib import Path
import threading
import time
import uuid


def path_signature(paths):
    entries = []
    for value in paths:
        path = Path(value)
        try:
            stat = path.stat()
            entry = (str(path), stat.st_dev, stat.st_ino, stat.st_mode,
                     stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        except OSError:
            entry = (str(path), None)
        entries.append(entry)
    return tuple(entries)


class SampleListingCache:
    # Recheck the full catalog periodically even on filesystems whose directory
    # timestamps do not reliably expose external changes (including mounted data).
    MAX_AGE_SECONDS = 30

    def __init__(self, paths, *, clock=time.monotonic):
        self.paths = paths
        self.clock = clock
        self.lock = threading.Lock()
        self.signature = None
        self.data = None
        self.payload = None
        self.loaded_at = 0

    def _signature(self):
        return path_signature(self.paths())

    def read(self, revision, loader):
        with self.lock:
            before = self._signature()
            fresh = (self.data is not None and before == self.signature
                     and self.clock() - self.loaded_at < self.MAX_AGE_SECONDS)
            # Explicit loads always inspect the catalog, including manual reloads
            # and reads after imports/deletes. Only polling uses a revision.
            if not revision or not fresh:
                data = loader()
                if self.data is None or data != self.payload:
                    self.payload = data
                    self.data = {**data, 'revision': uuid.uuid4().hex}
                self.loaded_at = self.clock()
                # A mutation overlapping the projection must invalidate the next
                # poll; never stamp an older projection with a newer signature.
                self.signature = before if before == self._signature() else None
            if revision == self.data['revision']:
                return {'unchanged': True, 'revision': revision}
            return self.data


class SampleCatalogCache:
    """Cache static sample metadata independently of busy OCR/result tables."""
    def __init__(self, library, *, clock=time.monotonic):
        self.library = library
        self.clock = clock
        self.lock = threading.Lock()
        self.signature = None
        self.data = None
        self.loaded_at = 0
        with library.database.connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS sample_catalog_revision (
                id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)''')
            con.execute('INSERT OR IGNORE INTO sample_catalog_revision VALUES(1,0)')
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                con.execute(f'''CREATE TRIGGER IF NOT EXISTS sample_catalog_{operation.lower()}
                    AFTER {operation} ON sample_library BEGIN
                    UPDATE sample_catalog_revision SET revision=revision+1 WHERE id=1;
                    END''')

    def _signature(self):
        with self.library.database.connect() as con:
            revision = con.execute('SELECT revision FROM sample_catalog_revision WHERE id=1').fetchone()[0]
        paths = self.library.paths
        return revision, path_signature((paths.ground_truth_path, paths.data_dir, self.library.directory))

    def read(self, *, force=False):
        with self.lock:
            before = self._signature()
            if (force or self.data is None or before != self.signature
                    or self.clock() - self.loaded_at >= SampleListingCache.MAX_AGE_SECONDS):
                self.data = self.library.catalog()
                self.loaded_at = self.clock()
                self.signature = before if before == self._signature() else None
            return self.data
