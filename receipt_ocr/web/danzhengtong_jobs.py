"""Remote task operations never call the recognition submission synchronously."""
from flask import jsonify, request


def register_danzhengtong_routes(app, store, wake, *, prefix='/api'):
    @app.route(prefix + '/danzhengtong/control', methods=['GET', 'PATCH'])
    def dzt_control():
        try:
            changes = request.get_json(silent=True) if request.method == 'PATCH' else None
            if request.method == 'PATCH' and not isinstance(changes, dict):
                raise ValueError('请提供有效的单证通控制参数')
            output = store().remote_control(changes)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if request.method == 'PATCH':
            wake()
        return jsonify(output)

    @app.post(prefix + '/danzhengtong/jobs')
    def dzt_jobs():
        payload = request.get_json(silent=True) or {}
        try:
            output = store().remote_action(payload.get('ids'), payload.get('action'))
        except KeyError:
            return jsonify(error='单据任务不存在'), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 409
        wake()
        return jsonify(output), 202

    @app.get(prefix + '/jobs/<job_id>/danzhengtong')
    def dzt_detail(job_id):
        try:
            return jsonify(store().remote_detail(job_id))
        except KeyError:
            return jsonify(error='单证通任务不存在'), 404
