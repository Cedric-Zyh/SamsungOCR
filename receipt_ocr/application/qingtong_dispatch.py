"""Own QingTong request scheduling and settle it before stage evaluation."""

from copy import deepcopy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import time

from ..domain.stages import STAGES
from ..runtime.progress import progress_context, report_model_progress
from ..runtime.scope import provider_scope

from ..domain.seal_policy import has_seal_requirement
from .requests import QingTongEvidence


class QingTongDispatch:
    """One request per document, optionally started alongside page OCR.

    A local reader may reuse a selected remote request, but never starts one.
    Failed responses are cached too: a second reader cannot repeat the upload.
    """

    def __init__(self, client, source, *, future=None):
        self.client, self.source, self._future = client, source, future
        self._evidence = None

    def _settle(self):
        if self._evidence is None:
            try:
                response = (self._future.result() if self._future is not None
                            else self.client.recognize(self.source))
                self._evidence = QingTongEvidence(response=deepcopy(response))
            except Exception as exc:
                self._evidence = QingTongEvidence(error=str(exc))
        return self._evidence

    def bind(self, context, request):
        if request.seal_evidence is not None:
            return request
        if request.seal_mode == "local":
            if self._future is None or not has_seal_requirement(request.fields):
                return request
        elif request.seal_mode == "qingtong_only":
            if not context.allows_remote_seal:
                return request
        else:
            # Preserve the older combined-mode routing: a valid receipt may
            # request remote evidence even before its footer is confirmed.
            kind = context.document_type["type"]
            if kind not in {"receipt", "product_continuation", "unclassified"} or (
                kind == "product_continuation" and not context.has_footer
            ):
                return request
        return replace(request, seal_evidence=self._settle())


@contextmanager
def qingtong_dispatch(client, source, config):
    """Upload once while local stages run; comparison still waits for fields."""
    recognize = getattr(client, "recognize", None)
    has_local_work = any(
        method != "qingtong" for stage in STAGES for method in config.get(stage, [])
    )
    if (
        "qingtong" not in config["seal"]
        or not has_local_work
        or not callable(recognize)
    ):
        yield QingTongDispatch(client, source)
        return

    def request_seal():
        with progress_context(method='qingtong', method_label='清瞳', target='客户印章',
                              target_id='seal', channel='parallel'):
            report_model_progress('seal', 'started', message='并行上传与印章识别')
            started = time.perf_counter()
            try:
                with provider_scope({"qingtong"}):
                    result = recognize(source)
            except Exception:
                report_model_progress('seal', 'failed',
                                      elapsed_seconds=round(time.perf_counter() - started, 2))
                raise
            report_model_progress('seal', 'completed',
                                  elapsed_seconds=round(time.perf_counter() - started, 2))
            return result

    # Only network I/O runs here. Paddle stays on its existing serialized path.
    # Joining the worker also prevents a completed/failed run leaking work into
    # the next receipt. The API client's existing timeout still applies.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt-seal") as pool:
        yield QingTongDispatch(client, source, future=pool.submit(copy_context().run, request_seal))
