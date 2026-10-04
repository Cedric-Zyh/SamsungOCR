"""Small persisted page projections used to select logical receipts.

Full OCR evidence is fetched only after filtering and pagination. SQLite
triggers maintain projections in the same transaction as every result write.
"""
from __future__ import annotations


def summary_expression(source: str) -> str:
    # SQL identifiers here are supplied by this module, never by a request.
    safe = f"CASE WHEN json_valid({source}) THEN {source} ELSE '{{}}' END"
    def extract(path):
        return f"json_extract({safe}, '$.{path}')"
    keys = ("fields", "internal_fields", "document_type", "field_schema_version", "ocr_backend")
    pairs = [f"'{key}', {extract(key)}" for key in keys]
    for kind in ("date_check", "seal_check"):
        # Preserve JSON booleans; json_extract of a scalar boolean is an SQL
        # integer, which would change domain checks using `is False`.
        pairs.append(f"'{kind}', {extract(kind)}")
    override_keys = ("fields", "date_check", "seal_check", "review_status", "final_result", "overall")
    # json_patch drops absent (null) members, preserving override semantics.
    pairs.append("'page_review_override', json_patch('{}', json_object(" + ", ".join(
        f"'{key}', {extract('page_review_override.' + key)}" for key in override_keys
    ) + "))")
    # Absent dictionaries must stay absent, rather than becoming None.
    return "json_patch('{}', json_object(" + ", ".join(pairs) + "))"


def initialize_read_model(connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(results)")}
    if "summary_json" not in columns:
        connection.execute("ALTER TABLE results ADD COLUMN summary_json TEXT NOT NULL DEFAULT ''")
    expression = summary_expression("NEW.result_json")
    for event in ("INSERT", "UPDATE OF result_json"):
        name = "insert" if event == "INSERT" else "update"
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS results_summary_{name}
            AFTER {event} ON results BEGIN
                UPDATE results SET summary_json={expression} WHERE id=NEW.id;
            END""")
    connection.execute(f"UPDATE results SET summary_json={summary_expression('result_json')} WHERE summary_json=''")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_results_import_day ON results(substr(created_at,1,10),id) WHERE deleted_at=''")


SUMMARY_COLUMNS = ",".join((
    "id", "filename", "stored_name", "preview_name", "created_at", "updated_at",
    "overall", "review_status", "final_result", "human_note", "error_type", "error_message",
    "attempt", "task_id", "page_group", "parent_result_id", "page_index",
    "summary_json AS result_json",
))


def load_selected_receipts(connection, selected, decode, merge):
    if not selected:
        return []
    ids = {item["id"] for item in selected}
    for item in selected:
        ids.update((item.get("page_group") or {}).get("continuation_result_ids", []))
    physical = []
    ordered_ids = sorted(ids)
    for offset in range(0, len(ordered_ids), 500):
        chunk = ordered_ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT * FROM results WHERE id IN ({placeholders}) AND deleted_at=''", chunk
        ).fetchall()
        physical.extend(decode(row) for row in rows)
    projected = {item["id"]: item for item in merge(sorted(physical, key=lambda item: item["id"], reverse=True))}
    return [projected[item["id"]] for item in selected]
