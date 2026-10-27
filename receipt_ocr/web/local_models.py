"""Controls for this backend process; no recognition preferences are changed."""
from flask import jsonify, request

from ..persistence.constants import DEFAULT_RECOGNITION_SETTINGS
from ..providers.model_manager import local_model_manager, ModelsBusy
from .remote_settings import same_origin
from .settings import normalize_recognition_settings


def local_models_handler(database):
    saved = database.get_json_setting('recognition_defaults', DEFAULT_RECOGNITION_SETTINGS)
    try:
        plan = normalize_recognition_settings(saved)['recognition_config']
    except ValueError:
        plan = DEFAULT_RECOGNITION_SETTINGS['recognition_config']
    if request.method == 'GET':
        return jsonify(local_model_manager.status(plan))
    if not same_origin():
        return jsonify(error='请在软件设置页面操作本地模型。'), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {'enabled'} or type(payload['enabled']) is not bool:
        return jsonify(error='请选择加载或释放本地模型。'), 400
    try:
        status = local_model_manager.change(payload['enabled'], plan)
    except ModelsBusy as exc:
        return jsonify({**local_model_manager.status(plan), 'error': str(exc)}), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(status), 202 if status['operation'] else 200
