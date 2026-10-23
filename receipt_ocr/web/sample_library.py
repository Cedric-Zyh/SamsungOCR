"""Managed sample imports and durable removal, isolated from source datasets."""
from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from receipt_ocr.evaluation import load_ground_truth


class LibraryConflict(ValueError):
    pass


def sample_number(name):
    """Ignore image extensions and legacy numeric copy suffixes for deduplication."""
    stem = Path(name).stem.strip().casefold()
    return re.sub(r'^(\d+)\s*\(\d+\)$', r'\1', stem)


class SampleLibrary:
    MAX_FILE_BYTES = 20 * 1024 * 1024

    def __init__(self, ctx, database):
        self.ctx, self.database = ctx, database
        self.directory = database.path.parent / 'originals'
        self.directory.mkdir(exist_ok=True)
        with database.connect() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS sample_library (
                filename TEXT PRIMARY KEY, storage_name TEXT NOT NULL DEFAULT '',
                entry_json TEXT NOT NULL DEFAULT '{}', deleted_at TEXT NOT NULL DEFAULT ''
            )''')

    def catalog(self, *, include_deleted=False, connection=None):
        entries = load_ground_truth(self.ctx.GROUND_TRUTH_PATH)
        entries = {name: entry for name, entry in entries.items()
                   if isinstance(name, str) and Path(name).name == name
                   and (self.ctx.DATA_DIR / name).is_file()
                   and (self.ctx.DATA_DIR / name).resolve().parent == self.ctx.DATA_DIR.resolve()
                   and Path(name).suffix.lower() in self.ctx.ALLOWED_EXTENSIONS}
        with (nullcontext(connection) if connection is not None else self.database.connect()) as con:
            for row in con.execute('SELECT * FROM sample_library'):
                if ((row['storage_name'] and (self.directory / row['storage_name']).is_file())
                        or (not row['storage_name'] and row['filename'] in entries)):
                    entries[row['filename']] = json.loads(row['entry_json'])
                if row['deleted_at'] and not include_deleted:
                    entries.pop(row['filename'], None)
        return entries

    def source(self, name, *, include_deleted=False):
        if name not in self.catalog(include_deleted=include_deleted):
            raise KeyError(name)
        with self.database.connect() as con:
            row = con.execute('SELECT storage_name FROM sample_library WHERE filename=?', (name,)).fetchone()
        root = self.directory if row and row['storage_name'] else self.ctx.DATA_DIR
        source = (root / (row['storage_name'] if row and row['storage_name'] else name)).resolve()
        if source.parent != root.resolve() or not source.is_file():
            raise KeyError(name)
        return source

    def import_files(self, files, company):
        if not 1 <= len(files) <= 100:
            raise ValueError('每次请选择 1–100 张样单图片')
        if not isinstance(company, str) or len(company.strip()) > 200:
            raise ValueError('客户公司名称最多 200 字')
        prepared, total_bytes = [], 0
        for upload in files:
            name = str(upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
            if (not name or len(name) > 200 or any(ord(char) < 32 for char in name)
                    or Path(name).suffix.lower() not in self.ctx.ALLOWED_EXTENSIONS):
                raise ValueError('请选择 JPG、PNG、BMP 或 WebP 图片，文件名最多 200 字')
            data = upload.stream.read(self.MAX_FILE_BYTES + 1)
            if not data or len(data) > self.MAX_FILE_BYTES:
                raise ValueError(f'{name}：图片为空或超过 20 MB')
            total_bytes += len(data)
            if total_bytes > 100 * 1024 * 1024:
                raise ValueError('每次导入的图片总大小不能超过 100 MB')
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(BytesIO(data)) as image:
                        if image.format not in {'JPEG', 'PNG', 'BMP', 'WEBP'}:
                            raise ValueError('不支持的图片格式')
                        image.verify()
                    with Image.open(BytesIO(data)) as image:
                        image.load()
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError,
                    Image.DecompressionBombWarning) as exc:
                raise ValueError(f'{name}：无法读取图片，请选择完整的 JPG、PNG、BMP 或 WebP 图片') from exc
            prepared.append((name, data))
        created, imported, skipped = [], [], []
        try:
            with self.database.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                reserved = set(self.catalog(include_deleted=True, connection=con))
                reserved.update(row[0] for row in con.execute('SELECT filename FROM sample_library'))
                reserved.update(row[0] for row in con.execute('SELECT DISTINCT filename FROM recognition_jobs'))
                numbers = {sample_number(name) for name in reserved}
                for original, data in prepared:
                    number = sample_number(original)
                    if number in numbers:
                        skipped.append({'filename': original, 'number': number})
                        continue
                    name = original
                    numbers.add(number)
                    storage_name = uuid.uuid4().hex + Path(name).suffix.lower()
                    target = self.directory / storage_name
                    created.append(target)
                    target.write_bytes(data)
                    entry = {'fields': {'客户名称': company.strip()}}
                    con.execute('INSERT INTO sample_library(filename,storage_name,entry_json) VALUES(?,?,?)',
                                (name, storage_name, json.dumps(entry, ensure_ascii=False)))
                    imported.append({'filename': name, 'original_name': original})
        except Exception:
            for target in created:
                target.unlink(missing_ok=True)
            raise
        return {'imported': imported, 'skipped': skipped, 'company': company.strip() or '未分类'}

    def classify(self, *, company='未分类', requirement=None, filenames=None):
        from .sample_requirement_groups import classify_sample_groups
        return classify_sample_groups(self, company=company, requirement=requirement, filenames=filenames)

    def remove(self, names):
        if (not isinstance(names, list) or not 1 <= len(names) <= 5000
                or any(not isinstance(name, str) for name in names)):
            raise ValueError('请选择要删除的样单')
        names = list(dict.fromkeys(names))
        # Move files aside while the write transaction is open. Restore them
        # on failure; discard the temporary directory only after commit.
        moved = []
        with tempfile.TemporaryDirectory(prefix='sample-delete-', dir=self.database.path.parent) as trash:
            try:
                with self.database.connect() as con:
                    con.execute('BEGIN IMMEDIATE')
                    catalog = self.catalog(include_deleted=True, connection=con)
                    library = {row['filename']: row for row in con.execute('SELECT * FROM sample_library')}
                    if any(name not in catalog and name not in library for name in names):
                        raise LibraryConflict('部分样单已被删除，请刷新列表')
                    busy = {row[0] for row in con.execute(
                        "SELECT filename FROM recognition_jobs WHERE status IN ('awaiting_upload','queued','running')")}
                    if busy.intersection(names):
                        raise LibraryConflict('所选样单中有正在测试的图片，请等待测试结束后再删除')
                    targets, tasks = set(), set()

                    def own_path(root, name):
                        root = Path(root).resolve()
                        target = root / name
                        if Path(name).name == name and target.resolve().parent == root and target.exists():
                            targets.add(target)

                    for name in names:
                        row = library.get(name)
                        if row and row['storage_name']:
                            own_path(self.directory, row['storage_name'])
                        # Legacy samples are files in the sample dataset, not
                        # production uploads. Remove their source as well so
                        # ground-truth labels cannot republish them on restart.
                        own_path(self.ctx.DATA_DIR, name)
                        for result in con.execute('SELECT preview_name FROM results WHERE filename=?', (name,)):
                            preview = result['preview_name']
                            if preview:
                                own_path(self.database.path.parent / 'previews', preview)
                                own_path(self.database.path.parent / 'artifacts', Path(preview).stem)
                        tasks.update(row[0] for row in con.execute(
                            'SELECT DISTINCT task_id FROM recognition_jobs WHERE filename=?', (name,)))
                    for target in sorted(targets):
                        staged = Path(trash) / str(len(moved))
                        shutil.move(str(target), str(staged))
                        moved.append((target, staged))
                    for name in names:
                        # Foreign keys cascade job events and result audits.
                        con.execute('DELETE FROM recognition_jobs WHERE filename=?', (name,))
                        con.execute('DELETE FROM results WHERE filename=?', (name,))
                        con.execute('DELETE FROM sample_library WHERE filename=?', (name,))
                    from receipt_ocr.jobs.store import JobStore
                    for task in tasks:
                        count = con.execute('SELECT COUNT(*) FROM recognition_jobs WHERE task_id=?', (task,)).fetchone()[0]
                        if count:
                            con.execute('UPDATE batch_tasks SET total=? WHERE id=?', (count, task))
                            JobStore(self.database)._refresh(con, task)
                        else:
                            con.execute('DELETE FROM batch_tasks WHERE id=?', (task,))
            except Exception:
                for target, staged in reversed(moved):
                    shutil.move(str(staged), str(target))
                raise
        return {'deleted': names}
