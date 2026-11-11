"""One short request at a time; waiting never occupies the regular OCR worker."""
from dataclasses import replace
import hashlib
import json
import logging
import sqlite3
import threading
from time import perf_counter

from .danzhengtong_store import later, scope_plan, settings_identity
from ..domain.remote_tasks import (
    aggregate_remote, begin_upload, begin_submission, accept_submission,
    fail_submission, fail_operation, finish_query,
)
from ..persistence.database_clock import now_iso
from ..persistence.recognition_calls import trace_timestamp
from ..persistence.async_recognition_calls import remote_call, record_transport_phase
from .danzhengtong_seals import SealRequests, seal_state, pending_seal_submission


class DanzhengtongWorker(SealRequests):
    def __init__(self, store, service, *, logger=None, client_factory=None, transform=None):
        self.store, self.service = store, service
        self.logger = logger or logging.getLogger(__name__)
        self.client_factory, self.transform = client_factory, transform or (lambda value: value)
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()
        from .danzhengtong_images import ReturnedImageWorker
        self.images = ReturnedImageWorker(store, service, self.logger)

    def start(self):
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                self._wake.set()
                return
            self._stop.clear()
            self.images.start()
            self._thread = threading.Thread(target=self._run, name='danzhengtong-queue', daemon=True)
            self._thread.start()

    def wake(self):
        self._wake.set()

    def is_alive(self):
        return bool((self._thread is not None and self._thread.is_alive())
                    or self.images.is_alive())

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
        self.images.stop(timeout)

    def _client(self, task):
        from ..danzhengtong import Client, Settings
        if self.client_factory:
            return self.client_factory(task)
        settings = Settings.load()
        environment = json.loads(task['environment_json'])
        if environment:
            if environment.get('account') != settings_identity(settings)['account']:
                raise ValueError('单证通账号已变更，请切回原账号后继续查询')
            settings = replace(settings, **{key: value for key, value in environment.items() if key != 'account'})
        return Client(settings)

    def step(self):
        task = self.store.claim_remote()
        if task is None:
            return False
        operation = task.get('_operation', 'apply' if task['status'] == 'received' else 'page_query')
        try:
            if operation == 'apply':
                self._apply(task)
            elif operation == 'page_submit':
                self._submit(task)
            elif operation in {'seal_submit', 'seal_prepare'}:
                self._submit_seals(task)
            elif operation == 'seal_query':
                self._query_seals(task)
            else:
                self._query(task)
        except Exception as exc:
            from ..danzhengtong_errors import safe_error_text
            error = safe_error_text(exc, None)
            if operation == 'apply':
                self.store.update_remote(task, status='received', error_message=error,
                                         next_query_at=later(30), lease_until='')
            else:
                state = seal_state(task)
                if operation.startswith('page_'):
                    page = state['page']
                    page.update(fail_operation(page, req_uuid=task.get('req_uuid'),
                                               error=error, retry_at=later(30)))
                elif task.get('_child_index') is not None:
                    child = state['requests'][task['_child_index']]
                    child.update(fail_operation(child, req_uuid=child.get('req_uuid'),
                                                error=error, retry_at=later(30)))
                else:
                    state.update(prepared=True, preparation_error=error)
                self._finish_seal_step(task, state)
            self.logger.exception('Danzhengtong background operation failed')
        return True

    def _finish_page(self, task, state, **changes):
        # Acceptance must be durable before local seal preparation or another
        # branch. Retrying this write never repeats the provider request.
        while not self._stop.is_set():
            try:
                if not self._save_seals(task, state, **{**aggregate_remote(state), **changes}):
                    return
                break
            except sqlite3.OperationalError:
                self._stop.wait(.5)
        else:
            return
        if state.get('seal_required') and not state.get('prepared'):
            self._prepare_seals(task, state)
        else:
            self._finish_seal_step(task, state)


    def _submit(self, task):
        from ..danzhengtong_errors import safe_error_text
        from ..runtime.calls import RecognitionCalls, capture_recognition_calls, recognition_call
        from ..runtime.progress import model_stage
        state = seal_state(task)
        page = state['page']
        client = self._client(task)
        client._validate_credentials()
        job = self.store.get(task['job_id'])
        source = self.service.source_for(job)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        previous = json.loads(task['recognition_call_json'] or '{}')
        transport = previous.get('transport') or {
            'upload_seconds': None, 'submit_seconds': None, 'query_seconds': 0.0, 'query_count': 0,
            'request_seconds': 0.0, 'measured_request_seconds': 0.0, 'elapsed_seconds': None,
            'elapsed_scope': 'upload_to_result', 'started_at': trace_timestamp(),
            'finished_at': '', 'complete': True, '_unmeasured_phases': [],
        }
        if previous:
            # An interrupted upload has no measured completion. Preserve known
            # phases and keep its unmeasured interval explicitly incomplete.
            unknown = set(transport.get('_unmeasured_phases', [])) | {'upload'}
            transport = {**transport, 'complete': False, 'request_seconds': None,
                         '_unmeasured_phases': sorted(unknown)}
        transport['phase'] = 'uploading'
        call = {'stage': 'routing', 'stage_label': '整页文字与版式', 'method': 'danzhengtong',
                'method_label': '单证通', 'action': '整页单证识别', 'input_name': job['filename'],
                'started_at': transport['started_at'], 'seconds': None, 'timing_recorded': True,
                'recognition_invoked': False, 'transport': transport,
                **RecognitionCalls(source).input_metadata(source)}
        call['input_name'] = job['filename']
        page.update(begin_upload())
        if not self._save_seals(task, state, environment_json=json.dumps(settings_identity(client.settings)),
                                source_hash=digest, recognition_call_json=json.dumps(call, ensure_ascii=False)):
            return
        # Upload can be retried explicitly. Submission is marked before POST so
        # recovery can distinguish an uncertain acceptance from an unsent task.
        upload_started_at = trace_timestamp()
        upload_started = perf_counter()
        try:
            uploaded = client.upload_file(source, file_name=job['filename'])
        except Exception as exc:
            call = record_transport_phase(call, task, 'upload', perf_counter() - upload_started,
                                          trace_timestamp(), terminal=True,
                                          started_at=upload_started_at, status='failed')
            call['error'] = f'上传失败（{type(exc).__name__}）'
            page.update(fail_submission(safe_error_text(exc, client.settings), submitted=False))
            self._finish_page(task, state, recognition_call_json=json.dumps(call, ensure_ascii=False))
            return
        call = record_transport_phase(call, task, 'upload', perf_counter() - upload_started, trace_timestamp(),
                                      started_at=upload_started_at)
        call['transport']['phase'] = 'submitting'
        page.update(begin_submission())
        if not self._save_seals(task, state, file_id=uploaded['fileId'], status='submitting',
                                recognition_call_json=json.dumps(call, ensure_ascii=False)):
            return
        task['_submission_started'] = True
        submit_started = None
        submit_started_at = None
        try:
            with capture_recognition_calls(source) as captured, model_stage('routing', 'danzhengtong'):
                with recognition_call('danzhengtong', '整页单证识别', source):
                    submit_started_at = trace_timestamp()
                    submit_started = perf_counter()
                    try:
                        body = client.submit(job['filename'], uploaded['fileId'])
                    finally:
                        submit_seconds = round(max(0, perf_counter() - submit_started), 4)
                        submit_finished_at = trace_timestamp()
        except Exception as exc:
            snapshot = getattr(exc, 'recognition_calls', None)
            measured = (snapshot.get('calls') or [None])[0] if snapshot else None
            if submit_started is None:
                raise  # No request was attempted; retain the durable upload record.
            call = {**call, **(measured or {}), 'recognition_invoked': bool(measured),
                    'input_name': job['filename'], 'seconds': submit_seconds if measured else None}
            call = record_transport_phase(call, task, 'submit', submit_seconds, submit_finished_at, terminal=True,
                                          started_at=submit_started_at, status='failed')
            page.update(fail_submission(safe_error_text(exc, client.settings), submitted=True,
                                        rejected=getattr(exc, 'provider_rejected', False)))
            self._finish_page(task, state, recognition_call_json=json.dumps(call, ensure_ascii=False))
            return
        call = {**call, **captured.snapshot()['calls'][0], 'recognition_invoked': True,
                'input_name': job['filename'], 'timing_recorded': True, 'seconds': submit_seconds}
        call = record_transport_phase(call, task, 'submit', submit_seconds, submit_finished_at,
                                      started_at=submit_started_at)
        # Persist the provider's accepted ID before doing any GET or local work.
        page.update(accept_submission(next_query_at=later(2)))
        self._finish_page(task, state, req_uuid=body['data']['reqUuid'],
                          submission_json=json.dumps(body, ensure_ascii=False), submitted_at=now_iso(),
                          recognition_call_json=json.dumps(call, ensure_ascii=False))

    def _query(self, task):
        from ..danzhengtong_errors import safe_error_text
        state = seal_state(task)
        page = state['page']
        job = self.store.get(task['job_id'])
        call = remote_call(task, job['filename'])
        started = None
        started_at = None
        finished_at = None
        request_seconds = None
        request_status = 'failed'
        measured = None

        def measured_call(*, terminal=False):
            nonlocal measured
            if measured is None:
                measured = dict(call)
                if started is not None:
                    measured = record_transport_phase(call, task, 'query', request_seconds,
                                                      finished_at, terminal=terminal,
                                                      started_at=started_at, status=request_status)
                    if measured.get('seconds') is not None:
                        measured['seconds'] = round(measured['seconds'] + request_seconds, 4)
            return json.dumps(measured, ensure_ascii=False)

        try:
            client = self._client(task)
            client._validate_credentials()
            started_at = trace_timestamp()
            started = perf_counter()
            try:
                body = client.get_result(task['req_uuid'], timeout=(5, 15))
                request_status = 'succeeded'
            finally:
                request_seconds = max(0, perf_counter() - started)
                finished_at = trace_timestamp()
            data = body.get('data')
            if not isinstance(data, dict):
                raise ValueError('单证通查询返回的 data 格式错误')
            commit = data.get('commitResult')
            if commit is not None and not isinstance(commit, dict):
                raise ValueError('单证通查询返回的 commitResult 格式错误')
            if commit:
                page.update(finish_query(submitted_at=task['submitted_at'],
                                         observed_at=now_iso(), completed=True))
                self._finish_seal_step(task, state, result_json=json.dumps(data, ensure_ascii=False),
                    last_query_at=now_iso(), query_count=task['query_count'] + 1,
                    recognition_call_json=measured_call(terminal=True))
                return
            error = ''
        except Exception as exc:
            error = safe_error_text(exc, getattr(locals().get('client'), 'settings', None))
        page.update(finish_query(submitted_at=task['submitted_at'], observed_at=now_iso(),
                                 completed=False, error=error))
        self._finish_seal_step(task, state, last_query_at=now_iso(),
            query_count=task['query_count'] + int(started is not None), recognition_call_json=measured_call())

    def _apply(self, task):
        from ..application.deferred_document import combine_saved, empty_snapshot
        job = self.store.get(task['job_id'])
        branch = json.loads(job['branch_snapshot_json'])
        options = json.loads(job['payload_json'])
        remote_only = job['status'] == 'queued' and options.get('_execution_scope') == 'danzhengtong'
        if remote_only and not options.get('_preserve_provider_results'):
            plan = json.loads(task['plan_json'])
            for stage, methods in plan.items():
                if isinstance(methods, list):
                    plan[stage] = [method for method in methods if method == 'danzhengtong']
            snapshot = empty_snapshot(plan, options.get('previous_fields'))
            snapshot['source_hash'] = task['source_hash']
            branch = {'snapshot': snapshot, 'base': {'filename': job['filename'], 'processing_seconds': 0}}
        if not branch.get('snapshot'):
            self.store.update_remote(task, status='conflict', error_message='历史记录缺少独立识别证据，已保留单证通结果，请人工核对', lease_until='')
            return
        snapshot = branch['snapshot']
        if snapshot.get('source_hash') and snapshot['source_hash'] != task['source_hash']:
            self.store.update_remote(task, status='conflict', error_message='图片版本已变化，已保留单证通返回结果，请核对后使用', lease_until='')
            return
        plan = json.loads(task['plan_json']) or scope_plan(json.loads(job['payload_json']))
        for stage in ('fields', 'handwriting', 'date', 'seal'):
            if 'danzhengtong' in plan[stage] and 'danzhengtong' not in snapshot['config'][stage]:
                snapshot['config'][stage].append('danzhengtong')
        state = seal_state(task)
        page_call = json.loads(task['recognition_call_json'] or '{}')
        apply_started = trace_timestamp()
        if isinstance(page_call.get('transport'), dict):
            page_call['transport']['apply_started_at'] = apply_started
        from ..danzhengtong import Settings
        download_images = Settings.load().download_seal_images
        for child in state.get('requests', []):
            transport = (child.get('call') or {}).get('transport')
            if isinstance(transport, dict):
                transport['apply_started_at'] = apply_started
            urls = (child.get('result') or {}).get('imgUrls')
            if (child.get('status') in {'completed', 'received'} and isinstance(urls, list)
                    and any(isinstance(url, str) and url for url in urls)):
                child['image_download'] = {'status': 'queued' if download_images else 'disabled'}
        if not self._save_seals(task, state, recognition_call_json=json.dumps(page_call, ensure_ascii=False)):
            return
        page = state['page']
        trace = {'mode': 'real', 'simulated': False, 'status': page['status'], 'error': page.get('error', ''),
                 'reqUuid': task['req_uuid'],
                 'submission': json.loads(task['submission_json']), 'result': json.loads(task['result_json']),
                 'query': {'attempts': (page_call.get('transport') or {}).get('query_count', task['query_count']),
                           'status': 'completed'}}
        seal_remote = None
        if state.get('seal_required'):
            directory, prefix = self._seal_storage(task)
            seal_remote = {'requests': state.get('requests', []),
                           'download_mode': 'deferred',
                           'artifact_dir': str(directory), 'artifact_url_prefix': prefix,
                           'trace': {'mode': 'real', 'simulated': False,
                                     'status': 'completed', 'model_id': json.loads(task['environment_json']).get('seal_model_id'),
                                     'requests': state.get('requests', []),
                                     'preparation_error': state.get('preparation_error', '')},
                           'preparation_error': state.get('preparation_error', '')}
        if not state.get('page_required'):
            trace = None
        combined = combine_saved(self.service.source_for(job), job['filename'], snapshot, remote=trace,
                                 remote_seal=seal_remote,
                                 preserve_provider_checks=bool(options.get('_preserve_provider_results')))
        if options.get('_preserve_provider_results'):
            from ..application.retained_providers import merge_retained_result
            combined = merge_retained_result(branch, combined)
        from ..domain.seal_standards import apply_standard_check
        requirement = (combined.get('fields') or {}).get('签章要求', '')
        combined = apply_standard_check(combined, self.store.database.get_seal_standard(requirement))
        for key in ('processing_seconds', 'processing_timings', 'queue', 'preview_url', 'filename'):
            if key in branch['base']:
                combined[key] = branch['base'][key]
        if remote_only:
            # This lane needs no local OCR placeholder. Finish directly even
            # while the regular queue is paused or has many earlier images.
            combined['_assembly_snapshot'] = snapshot
            self.store.finish(job, self.transform(combined), remote_only=True)
            self.store.update_remote(task, lease_until='')
            self.images.wake()
            return
        self.store.apply_remote(task, self.transform(combined))
        self.images.wake()

    def _run(self):
        from .worker import ProcessLock
        lock = ProcessLock(self.store.database.path.with_suffix('.dzt.worker.lock'))
        try:
            while not self._stop.is_set():
                if lock.acquire():
                    break
                self._stop.wait(1)
            else:
                return
            self.store.recover_remote()
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    worked = self.step()
                except Exception:
                    worked = False
                    self.logger.exception('Danzhengtong queue failed')
                self._wake.wait(.05 if worked else 1)
        finally:
            lock.release()
