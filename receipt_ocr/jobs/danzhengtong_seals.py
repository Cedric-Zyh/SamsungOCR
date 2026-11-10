"""Persist and advance stamp subrequests independently of the page request."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import hashlib
import json
import sqlite3
from time import perf_counter

from .danzhengtong_store import later, settings_identity, remote_state, aggregate_remote
from ..danzhengtong_errors import safe_error_text
from ..persistence.database_clock import now_iso
from ..persistence.recognition_calls import trace_timestamp
from ..persistence.async_recognition_calls import record_transport_phase


def seal_state(task):
    return remote_state(task)


def pending_seal_submission(task):
    state = seal_state(task)
    return bool(state and (not state.get('prepared') or
                          any(child.get('status') == 'queued' for child in state.get('requests', []))))


def skip_unsubmitted_shippers(state, source=None):
    """Retire old prepared shipper crops without repeating an uncertain POST."""
    requests = state.get('requests', [])
    if not any(child.get('status') == 'queued' for child in requests):
        return False
    senders = {index for index, child in enumerate(requests)
               if (child.get('region') or {}).get('role') == '发货单位章'}
    if source is not None:
        from ..danzhengtong_seal import sender_region_indices
        # Keep all regions together: the paired footer geometry can identify
        # an old left shipper crop whose saved role incorrectly says customer.
        senders.update(sender_region_indices(source, [child.get('region') or {} for child in requests]))
    changed = False
    for index, child in enumerate(requests):
        if child.get('status') != 'queued' or index not in senders:
            continue
        if child.get('req_uuid'):
            # Some old continuations can still have a queued marker despite
            # their accepted UUID. They need a GET, never another upload.
            child['status'] = 'waiting'
        else:
            call = child.get('call') or {}
            phase = (call.get('transport') or {}).get('phase', '')
            uncertain = (child.get('submission_unknown') or child.get('submission_started')
                         or child.get('_submission_started') or child.get('submitted_at')
                         or child.get('submission') or call.get('recognition_invoked')
                         or call.get('submission_unknown') or phase in {'submitting', 'submit_finished'})
            if uncertain:
                child.update(status='unknown', error=child.get('error') or
                             '历史发货单位章提交状态待确认，保留原请求，不重复提交')
            else:
                child.update(status='skipped', skip_reason='按印章位置判断为发货单位章，跳过单证通印章上传',
                             skipped_at=now_iso())
                child.pop('error', None)
        changed = True
    return changed


class SealRequests:
    def _seal_storage(self, task):
        directory = Path(self.service.documents.artifact_dir) / ('dzt-' + task['id'])
        return directory, '/files/artifacts/dzt-' + task['id']

    def _save_seals(self, task, state, **changes):
        encoded = json.dumps(state, ensure_ascii=False)
        updated = self.store.update_remote(task, seal_requests_json=encoded, **changes)
        if updated:
            task.update(seal_requests_json=encoded, **changes)
        return updated

    def _seal_client(self, task):
        if self.client_factory:
            return self.client_factory({**task, '_seal_request': True})
        from ..danzhengtong import Client
        page_client = self._client(task)
        settings = replace(page_client.settings, model_id=page_client.settings.seal_model_id)
        return Client(settings, transport=page_client.transport, action='印章专用场景识别')

    def _prepare_seals(self, task, state, *, finish=True):
        """A crop/detection error belongs to seals, never the page request."""
        if not state.get('seal_required') or state.get('prepared'):
            return True
        try:
            from ..danzhengtong_seal import prepare_requests
            from ..imaging.working_images import image_workspace_scope
            from ..providers.model_manager import local_model_manager
            client = self._client(task)
            client._validate_credentials()
            job = self.store.get(task['job_id'])
            source = self.service.source_for(job)
            directory, prefix = self._seal_storage(task)
            # This worker runs outside the regular recognition service: keep
            # its detector alive and share one decoded page across all crops.
            with local_model_manager.activity(), image_workspace_scope():
                requests = prepare_requests(source, directory, prefix)
            state.update(prepared=True, requests=[dict(item, status='queued', req_uuid='', query_count=0,
                                                      next_query_at='') for item in requests])
            changes = {'source_hash': task.get('source_hash') or hashlib.sha256(source.read_bytes()).hexdigest(),
                       'environment_json': json.dumps(settings_identity(client.settings))}
        except Exception as exc:
            state.update(prepared=True, requests=[], preparation_error=safe_error_text(exc, getattr(locals().get('client'), 'settings', None)))
            changes = {}
        if finish:
            return self._finish_seal_step(task, state, **changes)
        return self._save_seals(task, state, **changes)

    def _submit_seals(self, task):
        state = seal_state(task)
        if not self._prepare_seals(task, state, finish=False):
            return
        job = self.store.get(task['job_id'])
        source = self.service.source_for(job)
        if skip_unsubmitted_shippers(state, source) and not self._save_seals(task, state):
            return
        child = next((item for item in state['requests'] if item['status'] == 'queued'), None)
        if child is None:
            self._finish_seal_step(task, state)
            return
        client = self._seal_client(task)
        client._validate_credentials()
        path = Path(child['input_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != child['input_sha256']:
            child.update(status='failed', error='待上传印章图片已变化，请重新识别')
            self._finish_seal_step(task, state)
            return True
        from ..runtime.calls import RecognitionCalls
        directory, prefix = self._seal_storage(task)
        metadata = RecognitionCalls(source, directory, prefix).input_metadata(path)
        call = {'stage': 'seal', 'stage_label': '客户印章', 'method': 'danzhengtong',
                'method_label': '单证通', 'action': '印章识别', 'input_name': path.name,
                'input_url': child.get('input_url', ''), **metadata, 'started_at': trace_timestamp(),
                'seconds': 0.0, 'timing_recorded': True, 'recognition_invoked': False,
                'transport': {'upload_seconds': 0.0, 'submit_seconds': 0.0, 'query_seconds': 0.0,
                              'query_count': 0, 'request_seconds': 0.0, 'measured_request_seconds': 0.0,
                              'elapsed_seconds': None, 'elapsed_scope': 'upload_to_result',
                              'started_at': trace_timestamp(), 'finished_at': '', 'complete': True,
                              '_unmeasured_phases': []}}
        child['call'] = call
        child['status'] = 'uploading'
        if not self._save_seals(task, state):
            return True
        phase, started_at, started = 'upload', trace_timestamp(), perf_counter()
        try:
            uploaded = client.upload_file(path)
            call = record_transport_phase(call, child, phase, perf_counter() - started, trace_timestamp(),
                                          started_at=started_at)
            child.update(file_id=uploaded['fileId'], status='submitting', call=call)
            call['transport']['phase'] = 'submitting'
            # Mark the individual POST uncertain before it can reach the
            # provider. Recovery must never repeat it without an accepted UUID.
            if not self._save_seals(task, state):
                return True
            phase, started_at, started = 'submit', trace_timestamp(), perf_counter()
            call['recognition_invoked'] = True
            body = client.submit(path.name, uploaded['fileId'])
            call = record_transport_phase(call, child, phase, perf_counter() - started, trace_timestamp(),
                                          started_at=started_at)
            call.update(recognition_invoked=True, seconds=call['transport']['submit_seconds'])
            child.update(req_uuid=body['data']['reqUuid'], submission=body, submitted_at=now_iso(),
                         status='waiting', next_query_at=later(2), call=call)
            # Persist accepted UUID before querying or considering another
            # image; only this database write is repeated on transient locks.
            while not self._stop.is_set():
                try:
                    self._finish_seal_step(task, state)
                    return True
                except sqlite3.OperationalError:
                    self._stop.wait(.5)
            return True
        except Exception as exc:
            call = record_transport_phase(call, child, phase, perf_counter() - started, trace_timestamp(),
                                          started_at=started_at, terminal=True, status='failed')
            unknown = phase == 'submit' and not getattr(exc, 'provider_rejected', False)
            child.update(status='unknown' if unknown else 'failed', call=call,
                         error=safe_error_text(exc, client.settings))
            self._finish_seal_step(task, state)
            return True

    def _finish_seal_step(self, task, state, **changes):
        aggregate = aggregate_remote(state)
        return self._save_seals(task, state, **{**aggregate, **changes, 'lease_until': ''})

    def _query_seals(self, task):
        state = seal_state(task)
        if not state:
            return False
        if any(child.get('status') == 'queued' for child in state.get('requests', [])):
            job = self.store.get(task['job_id'])
            if skip_unsubmitted_shippers(state, self.service.source_for(job)) and not self._save_seals(task, state):
                return True
        waiting = [item for item in state.get('requests', []) if item.get('status') == 'waiting']
        position = task.get('_child_index')
        child = (state['requests'][position] if position is not None else
                 min(waiting, key=lambda item: item.get('last_query_at', '')) if waiting else None)
        if child is None:
            self._finish_seal_step(task, state)
            return True
        client = self._seal_client(task)
        call = child['call']
        started_at, started = trace_timestamp(), perf_counter()
        try:
            body = client.get_result(child['req_uuid'], timeout=(5, 15))
            data = body.get('data')
            if not isinstance(data, dict) or (data.get('commitResult') is not None
                                               and not isinstance(data['commitResult'], dict)):
                raise ValueError('单证通印章查询返回格式错误')
            complete = bool(data.get('commitResult'))
            call = record_transport_phase(call, child, 'query', perf_counter() - started,
                                          trace_timestamp(), started_at=started_at, terminal=complete)
            call['seconds'] = round(call.get('seconds', 0) + call['transport']['spans'][-1]['seconds'], 4)
            child.update(status='completed' if complete else 'waiting', result=deepcopy(data), call=call)
            child.pop('error', None)
        except Exception as exc:
            measured = record_transport_phase(
                call, child, 'query', perf_counter() - started, trace_timestamp(),
                started_at=started_at, status='failed')
            measured['seconds'] = round(call.get('seconds', 0) + measured['transport']['spans'][-1]['seconds'], 4)
            child.update(error=safe_error_text(exc, client.settings), call=measured)
        elapsed = (datetime.now().astimezone() - datetime.fromisoformat(child['submitted_at'])).total_seconds()
        delay = 2 if elapsed < 60 else 30 if elapsed < 600 else 120
        if child.get('error'):
            delay = max(30, delay)
        child.update(last_query_at=now_iso(), query_count=child.get('query_count', 0) + 1,
                     next_query_at=later(delay) if child['status'] == 'waiting' else '')
        self._finish_seal_step(task, state, last_query_at=now_iso(), query_count=task['query_count'] + 1)
        return True
