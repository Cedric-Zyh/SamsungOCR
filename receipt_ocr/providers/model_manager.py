"""Process-local model loading and release, shared by all receipt entry points."""
from contextlib import contextmanager
import gc
import logging
from threading import Condition, Thread
from time import perf_counter


MODEL_LABELS = {
    'page_v6': '整页文字识别',
    'line_v6': '文字行识别',
    'seal': '印章专用识别',
    'line_orientation': '文字行方向识别',
    'doc_orientation': '印章方向识别',
}


def required_models(plan):
    """Prepare only local routes that the saved recognition plan can reach."""
    local = {'paddle_v6', 'paddle_seal'}
    stages = {key: set(plan.get(key) or []) for key in ('fields', 'products', 'handwriting', 'date', 'seal')}
    from ..recognition.seal.reading_policy import secondary_read_providers_for_plan
    local_seal = bool(stages['seal'] & local or secondary_read_providers_for_plan(plan))
    needed = set()
    if any(methods & local for methods in stages.values()) or local_seal:
        needed.add('page_v6')
    if 'paddle_v6' in stages['date'] or local_seal:
        needed.add('line_v6')
    if 'paddle_seal' in stages['seal']:
        needed.add('seal')
    if local_seal and plan.get('seal_orientation', 'polygon') != 'none':
        # Rectangular stamps use the line classifier in every orientation mode.
        needed.add('line_orientation')
        if plan.get('seal_orientation') == 'doc_ori':
            needed.add('doc_orientation')
    return [key for key in MODEL_LABELS if key in needed]


def loaded_models():
    from . import paddle_runtime as paddle, orientation
    return {
        'page_v6': 'v6' in paddle._PIPELINES,
        'line_v6': 'v6' in paddle._LINE_RECOGNIZERS,
        'seal': 'seal' in paddle._SEAL_DETECTORS,
        'line_orientation': orientation._classifier is not None,
        'doc_orientation': orientation._doc_classifier is not None,
    }


def _load_model(key):
    from . import paddle_runtime as paddle, orientation
    return {
        'page_v6': lambda: paddle._pipeline('v6'),
        'line_v6': lambda: paddle._line_recognizer('v6'),
        'seal': paddle._seal_pipeline,
        'line_orientation': orientation._line_classifier,
        'doc_orientation': orientation._document_classifier,
    }[key]()


def _release_models():
    from . import paddle_runtime as paddle, orientation
    # The manager owns the prediction lock here; no caller can retain a model
    # for inference while these references are being removed.
    with paddle._PIPELINE_LOCK, orientation._lock:
        paddle._PIPELINES.clear()
        paddle._LINE_RECOGNIZERS.clear()
        paddle._SEAL_DETECTORS.clear()
        orientation._classifier = None
        orientation._doc_classifier = None
    gc.collect()


class ModelsBusy(RuntimeError):
    pass


class LocalModelManager:
    def __init__(self):
        self._condition = Condition()
        self._active = 0
        self._operation = ''
        self._error = ''
        self._seconds = None
        self._thread = None

    @contextmanager
    def activity(self):
        with self._condition:
            while self._operation:
                self._condition.wait()
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def status(self, plan):
        needed = required_models(plan)
        with self._condition:
            loaded = loaded_models()
            count = sum(loaded.values())
            ready = bool(needed) and all(loaded[key] for key in needed)
            state = (self._operation or ('busy' if self._active else
                     'ready' if ready else 'partial' if count else
                     'unloaded' if needed else 'unneeded'))
            return dict(state=state, loaded=bool(count), ready=ready,
                        busy=bool(self._active), operation=self._operation,
                        error=self._error, initialization_seconds=self._seconds,
                        required_count=len(needed), loaded_count=count,
                        models=[dict(id=key, label=label, required=key in needed, loaded=loaded[key])
                                for key, label in MODEL_LABELS.items() if key in needed or loaded[key]])

    def change(self, enabled, plan):
        needed = required_models(plan)
        with self._condition:
            if self._active or self._operation:
                raise ModelsBusy('正在识别或准备模型，请完成后再操作。')
            if enabled and not needed:
                raise ValueError('当前方案没有需要加载的本地模型。')
            loaded = loaded_models()
            if (enabled and all(loaded[key] for key in needed)) or (not enabled and not any(loaded.values())):
                self._error = ''
                return self.status(plan)
            self._operation = 'loading' if enabled else 'unloading'
            self._error = ''
            self._thread = Thread(target=self._perform, args=(enabled, needed),
                                  name='receipt-local-models', daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._operation = ''
                self._condition.notify_all()
                raise
            return self.status(plan)

    def _perform(self, enabled, needed):
        started = perf_counter()
        try:
            from .paddle_runtime import _PREDICT_LOCK
            with _PREDICT_LOCK:
                if enabled:
                    for key in needed:
                        _load_model(key)
                else:
                    _release_models()
        except Exception as exc:
            logging.getLogger(__name__).exception('Local model operation failed')
            with self._condition:
                self._error = f"{'初始化' if enabled else '释放'}失败（{type(exc).__name__}），请重试。"
        finally:
            with self._condition:
                if enabled:
                    self._seconds = round(perf_counter() - started, 4)
                self._operation = ''
                self._condition.notify_all()


local_model_manager = LocalModelManager()
