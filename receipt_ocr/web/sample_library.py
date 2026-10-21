"""Managed sample imports and durable removal, isolated from source datasets."""
from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.persistence.database import now_iso


class LibraryConflict(ValueError):
    pass


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
                if row['storage_name'] and (self.directory / row['storage_name']).is_file():
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
        created, imported = [], []
        try:
            with self.database.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                reserved = set(self.catalog(include_deleted=True, connection=con))
                reserved.update(row[0] for row in con.execute('SELECT filename FROM sample_library'))
                reserved.update(row[0] for row in con.execute('SELECT DISTINCT filename FROM recognition_jobs'))
                for original, data in prepared:
                    name, index = original, 2
                    while name in reserved:
                        name = f'{Path(original).stem} ({index}){Path(original).suffix}'
                        index += 1
                    reserved.add(name)
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
        return {'imported': imported, 'company': company.strip() or '未分类'}

    def remove(self, names):
        if (not isinstance(names, list) or not 1 <= len(names) <= 5000
                or any(not isinstance(name, str) for name in names)):
            raise ValueError('请选择要删除的样单')
        names = list(dict.fromkeys(names))
        with self.database.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            catalog = self.catalog(connection=con)
            if any(name not in catalog for name in names):
                raise LibraryConflict('部分样单已被删除，请刷新列表')
            busy = {row[0] for row in con.execute(
                "SELECT filename FROM recognition_jobs WHERE status IN ('awaiting_upload','queued','running')")}
            if busy.intersection(names):
                raise LibraryConflict('所选样单中有正在测试的图片，请等待测试结束后再删除')
            for name in names:
                con.execute('''INSERT INTO sample_library(filename,entry_json,deleted_at) VALUES(?,?,?)
                    ON CONFLICT(filename) DO UPDATE SET deleted_at=excluded.deleted_at''',
                    (name, json.dumps(catalog[name], ensure_ascii=False), now_iso()))
        return {'deleted': names}
