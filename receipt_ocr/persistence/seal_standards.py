"""One explicit standard per requirement, independent of receipt retention."""
from contextlib import nullcontext

from .database_clock import now_iso
from ..domain.seal_standards import requirement_key


def _validate_horizontal_text(value, shape):
    if not isinstance(value, str) or value not in {'unmarked', 'present', 'absent'}:
        raise ValueError('请选择有、无或未标注')
    if shape not in {'round', 'ellipse'} and value != 'unmarked':
        raise ValueError('仅圆章和椭圆章可设置章内横排文字')


def initialize_seal_standards(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS seal_standards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requirement_key TEXT NOT NULL UNIQUE,
        requirement TEXT NOT NULL,
        image_name TEXT NOT NULL,
        recognized TEXT NOT NULL,
        shape TEXT NOT NULL,
        color TEXT NOT NULL,
        source_result_id INTEGER NOT NULL,
        source_filename TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1
    )""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(seal_standards)")}
    if "source_kind" not in columns:
        connection.execute("ALTER TABLE seal_standards ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'receipt'")
    if "recognized_source" not in columns:
        connection.execute("ALTER TABLE seal_standards ADD COLUMN recognized_source TEXT NOT NULL DEFAULT 'ocr'")
    if "ocr_recognized" not in columns:
        connection.execute("ALTER TABLE seal_standards ADD COLUMN ocr_recognized TEXT NOT NULL DEFAULT ''")
        connection.execute("UPDATE seal_standards SET ocr_recognized=recognized")
    if "horizontal_text" not in columns:
        connection.execute("ALTER TABLE seal_standards ADD COLUMN horizontal_text TEXT NOT NULL DEFAULT 'unmarked'")


def standard_record(row):
    if row is None:
        return None
    item = dict(row)
    item["image_url"] = f"/files/seal-standards/{item.pop('image_name')}"
    return item


class SealStandardOperationsMixin:
    def get_seal_standard(self, requirement, *, _connection=None):
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            return standard_record(connection.execute(
                "SELECT * FROM seal_standards WHERE requirement_key=?", (requirement_key(requirement),)
            ).fetchone())

    def list_seal_standards(self, search=""):
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM seal_standards ORDER BY updated_at DESC,id DESC").fetchall()
        needle = requirement_key(search).casefold()
        return [standard_record(row) for row in rows if not needle or needle in row["requirement_key"].casefold()]

    def save_seal_standard(self, values, *, expected_revision, _connection=None):
        _validate_horizontal_text(values.get('horizontal_text', 'unmarked'), values.get('shape'))
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            current = self.get_seal_standard(values["requirement"], _connection=connection)
            revision = current["revision"] if current else 0
            if type(expected_revision) is not int or expected_revision != revision:
                raise ValueError("标准章已变化，请刷新后重新选择；已有标准章必须明确执行替换")
            timestamp = now_iso()
            data = {"source_kind": "receipt", "recognized_source": "ocr",
                    "horizontal_text": "unmarked",
                    "ocr_recognized": values["recognized"], **values, "requirement_key": requirement_key(values["requirement"]),
                    "created_at": current["created_at"] if current else timestamp,
                    "updated_at": timestamp, "revision": revision + 1}
            connection.execute("""INSERT INTO seal_standards(
                requirement_key,requirement,image_name,recognized,shape,color,source_result_id,
                source_filename,candidate_id,created_at,updated_at,revision,source_kind,recognized_source,ocr_recognized,horizontal_text
            ) VALUES(:requirement_key,:requirement,:image_name,:recognized,:shape,:color,:source_result_id,
                :source_filename,:candidate_id,:created_at,:updated_at,:revision,:source_kind,:recognized_source,:ocr_recognized,:horizontal_text)
            ON CONFLICT(requirement_key) DO UPDATE SET image_name=excluded.image_name,
                recognized=excluded.recognized,shape=excluded.shape,color=excluded.color,
                source_result_id=excluded.source_result_id,source_filename=excluded.source_filename,source_kind=excluded.source_kind,
                recognized_source=excluded.recognized_source,ocr_recognized=excluded.ocr_recognized,
                horizontal_text=excluded.horizontal_text,
                candidate_id=excluded.candidate_id,updated_at=excluded.updated_at,revision=excluded.revision""", data)
            return self.get_seal_standard(values["requirement"], _connection=connection)

    def update_seal_standard_layout(self, standard_id, horizontal_text, *, expected_revision):
        if not isinstance(horizontal_text, str) or horizontal_text not in {'unmarked', 'present', 'absent'}:
            raise ValueError('请选择有、无或未标注')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM seal_standards WHERE id=?', (standard_id,)).fetchone()
            if row is None:
                raise KeyError(standard_id)
            if row['shape'] not in {'round', 'ellipse'}:
                raise ValueError('仅圆章和椭圆章可设置章内横排文字')
            if type(expected_revision) is not int or expected_revision != row['revision']:
                raise ValueError('标准章已变化，请刷新后重新设置')
            if row['horizontal_text'] != horizontal_text:
                connection.execute('UPDATE seal_standards SET horizontal_text=?,updated_at=?,revision=revision+1 WHERE id=?',
                                   (horizontal_text, now_iso(), standard_id))
            return standard_record(connection.execute('SELECT * FROM seal_standards WHERE id=?', (standard_id,)).fetchone())

    def seal_standard_sources(self, requirement, *, limit=60, pending_only=False, _connection=None):
        key = requirement_key(requirement)
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            # Query compact fields first, decode artifacts only for matching
            # requirements. Saved human field corrections are authoritative.
            rows = connection.execute("""SELECT id,COALESCE(
                json_extract(summary_json,'$.page_review_override.fields.签章要求'),
                json_extract(summary_json,'$.fields.签章要求')) AS requirement
                FROM results WHERE deleted_at='' AND (?=0 OR review_status='待复核') ORDER BY id DESC""",
                (int(pending_only),)).fetchall()
            ids = [row["id"] for row in rows if requirement_key(row["requirement"]) == key]
            if limit is not None:
                ids = ids[:limit]
            return [self.get_result(i, _connection=connection) for i in ids]
