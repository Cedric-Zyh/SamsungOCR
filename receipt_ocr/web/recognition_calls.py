"""HTTP projection of the latest measured OCR calls for one receipt."""

from flask import jsonify


def recognition_calls_response(database, result_id, *, samples=False):
    try:
        data = database.latest_recognition_calls(result_id)
    except KeyError:
        return jsonify(error="回单不存在"), 404
    if samples:
        # Failed sample executions bypass SampleCenter's successful-result
        # URL projection, but use the same sample artifact directory.
        for call in data["calls"]:
            url = call.get("image_url", "")
            if url.startswith("/files/artifacts/"):
                call["image_url"] = url.replace("/files/artifacts/", "/files/samples/artifacts/", 1)
    return jsonify(data)
