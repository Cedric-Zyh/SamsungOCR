"""Shared activity checks for sample admission and destructive operations."""


def active_job_filenames(connection, filenames=None):
    """Include remote continuations and image downloads after local OCR ends.

    Callers making a decision followed by a write must hold the same write
    transaction throughout. Terminal remote errors remain available for retry
    or explicit deletion; pending work must finish first.
    """
    names = None if filenames is None else list(dict.fromkeys(filenames))
    chunks = [None] if names is None else [names[i:i + 400] for i in range(0, len(names), 400)]
    active = set()
    for chunk in chunks:
        scope = '' if chunk is None else ' AND j.filename IN (' + ','.join('?' for _ in chunk) + ')'
        active.update(row[0] for row in connection.execute('''
            SELECT DISTINCT j.filename FROM recognition_jobs j
            WHERE (j.status IN ('awaiting_upload','queued','running') OR EXISTS (
                SELECT 1 FROM danzhengtong_jobs d WHERE d.job_id=j.id AND d.active=1
                  AND (d.status IN ('queued','submitting','waiting','received') OR
                       (d.status='applied' AND EXISTS (
                           SELECT 1 FROM json_each(d.seal_requests_json, '$.requests') child
                           WHERE json_extract(child.value, '$.image_download.status') IN ('queued','running')
                       )))
            ))''' + scope, chunk or []))
    return active
