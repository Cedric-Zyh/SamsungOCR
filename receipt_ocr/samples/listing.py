"""Cached sample catalog and result read model."""
from collections import Counter
import json

from receipt_ocr.domain.fields.schema import derive_signature_check
from receipt_ocr.domain.seal_standards import requirement_key
from .listing_cache import SampleCatalogCache, SampleListingCache
from .requirement_groups import group_name
from .result_filters import LIST_RESULT_SQL, SEAL_SOURCES, decode_list_result, sample_result_filters


class SampleListing:
    def __init__(self, database, library, company_marks, dependencies):
        self.database = database
        self.library = library
        self.company_marks = company_marks
        self.standards = dependencies.database
        self.paths = dependencies.paths
        self.cache = SampleListingCache(self._listing_paths)
        self.catalog_cache = SampleCatalogCache(library)

    def _listing_paths(self):
        # Main DB and WAL metadata detect committed writes from other workers or
        # app windows without scanning tables. Shared standards live in dependencies.database.
        for database in (self.database, self.standards):
            yield database.path
            yield str(database.path) + '-wal'
            yield str(database.path) + '-journal'
        yield self.paths.ground_truth_path
        yield self.paths.data_dir
        yield self.library.directory

    def listing(self, revision=None):
        return self.cache.read(revision, lambda: self._build_listing(force_catalog=not revision))

    def _build_listing(self, *, force_catalog=False):
        catalog = self.catalog_cache.read(force=force_catalog)
        # Only the latest job per filename is needed, not full OCR artifacts.
        with self.database.connect() as con:
            con.execute('BEGIN')
            jobs = {row["filename"]: dict(row) for row in con.execute(f"""
                SELECT j.id,j.task_id,j.filename,j.status,j.result_id,j.error_message,j.created_at,j.finished_at,j.payload_json,
                    {LIST_RESULT_SQL} AS list_result
                FROM recognition_jobs j
                LEFT JOIN results r ON r.id=j.result_id AND r.deleted_at=''
                WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
            """)}
            from receipt_ocr.persistence.job_views import PUBLIC_COLUMNS, remote_public
            remote = {row['job_id']: remote_public(row) for row in con.execute(f'SELECT {PUBLIC_COLUMNS} FROM danzhengtong_jobs WHERE active=1')}
            marks, versions = self.company_marks.snapshot(catalog, con)
        items = []
        for name, entry in catalog.items():
            job = jobs.get(name, {})
            result = decode_list_result(job['list_result']) if job else {}
            signature = derive_signature_check(result.get('fields') or {}, result.get('signature_check') or {})
            status = {"queued": "测试中", "running": "测试中", "succeeded": "已完成",
                      "failed": "运行失败"}.get(job.get("status"), "未测试")
            dzt = remote.get(job.get('id'))
            options = json.loads(job['payload_json']) if job else {}
            if dzt and dzt['pending']:
                status = '测试中'
            filters = sample_result_filters(job, entry, result=result)
            items.append({
                "filename": name,
                "company": str((entry.get("fields") or {}).get("客户名称") or "").strip() or "未分类",
                "requirement": group_name(entry),
                "requirement_key": requirement_key(group_name(entry)),
                "status": status, "job_status": job.get("status", ""),
                "danzhengtong": dzt, "job_id": job.get('id'),
                "task_id": job.get('task_id'), "job_created_at": job.get('created_at', ''),
                "execution_mode": options.get('_sample_execution_mode', ''),
                "retained_results": bool(options.get('_preserve_provider_results')),
                "result_id": job.get("result_id"), "error": job.get("error_message", ""),
                "updated_at": job.get("finished_at") or job.get("created_at", ""),
                "checks": {"date": filters['date']['state'], "seal": filters['seals']['combined']['state']},
                "signature": signature.get("status", "未执行"),
                "result_filters": filters,
            })
        counts = Counter(item["company"] for item in items)
        companies = [{"name": name, "count": count}
                     for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
        # Customer names can differ from the stamp owner. Use the same exact
        # normalized requirement key as the seal center, never a name substring.
        with self.standards.connect() as con:
            standard_keys = {row[0] for row in con.execute('SELECT requirement_key FROM seal_standards')}
        requirements = {name: set() for name in counts}
        unlabeled = Counter()
        for entry in catalog.values():
            fields = entry.get('fields') or {}
            name = str(fields.get('客户名称') or '').strip() or '未分类'
            key = requirement_key(fields.get('签章要求'))
            if key:
                requirements[name].add(key)
            else:
                unlabeled[name] += 1
        seals = {}
        for name, keys in requirements.items():
            matched = len(keys & standard_keys)
            seals[name] = {'matched': matched, 'total': len(keys), 'unlabeled': unlabeled[name],
                           'has_standard': matched > 0,
                           'complete': bool(keys) and matched == len(keys) and not unlabeled[name]}
        groups = {name: {} for name in counts}
        for item in items:
            key = item['requirement_key']
            group = groups[item['company']].setdefault(key, {
                'key': key, 'name': item['requirement'] or '未归类', 'count': 0,
                'has_standard': bool(key and key in standard_keys)})
            group['count'] += 1
        return {"items": items, "companies": companies, "total": len(items),
                "filter_seal_sources": SEAL_SOURCES,
                "requirement_groups": {name: sorted(values.values(), key=lambda group: (bool(group['key']), group['name']))
                                       for name, values in groups.items()},
                "company_marks": marks, "company_versions": versions, "company_seals": seals}
