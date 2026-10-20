from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace

from flask import Flask
from PIL import Image
import pytest

from receipt_ocr.persistence.database import Database, DEFAULT_RECOGNITION_SETTINGS
from receipt_ocr.web.samples import register_sample_routes, SampleCenter


@pytest.fixture
def samples(tmp_path):
    data = tmp_path / "samples"
    data.mkdir()
    truth = {
        "one.png": {"fields": {"客户名称": "甲公司", "承运商": "承运乙"}},
        "two.png": {"fields": {"客户名称": "甲公司", "承运商": "承运丙"}},
        "three.png": {"fields": {"客户名称": "乙公司"}},
        "blank.png": {"fields": {}},
        "missing.png": {"fields": {"客户名称": "不存在"}},
        "../outside.png": {"fields": {"客户名称": "目录外"}},
    }
    for name in ("one.png", "two.png", "three.png", "blank.png"):
        Image.new("RGB", (60, 90), "white").save(data / name)
    Image.new("RGB", (60, 90), "red").save(tmp_path / "outside.png")
    (data / "symlink.png").symlink_to(tmp_path / "outside.png")
    truth["symlink.png"] = {"fields": {"客户名称": "链接"}}
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps(truth), encoding="utf-8")
    db = Database(tmp_path / "production" / "results.db")
    db.initialize()
    app = Flask(__name__)
    app.config.update(TESTING=True, BACKGROUND_WORKER=False)

    def analyze(source, preview, **kwargs):
        Image.open(source).save(preview)
        artifact_dir = kwargs["artifact_dir"]
        artifact_dir.mkdir(parents=True)
        Image.open(source).save(artifact_dir / "date.png")
        return {"overall": "通过", "fields": truth[source.name]["fields"],
                "date_check": {"status": "匹配", "actual": "2026-03-27", "reliable": True},
                "seal_check": {"status": "匹配", "recognized": "示例章", "reliable": True},
                "processing_artifacts": {"date": [{"original_url": kwargs["artifact_url_prefix"] + "/date.png"}], "seals": []}}

    ctx = SimpleNamespace(database=db, app=app, DATA_DIR=data, UPLOAD_DIR=tmp_path / "uploads",
                          GROUND_TRUTH_PATH=truth_path, ALLOWED_EXTENSIONS={".png"},
                          analyzer=SimpleNamespace(seal_api=SimpleNamespace(enabled=False)),
                          _configured_analyze=analyze)
    register_sample_routes(app, ctx)
    return ctx, app.test_client()


def test_company_catalog_uses_customers_and_preview_never_creates_jobs(samples):
    ctx, client = samples
    listing = client.get("/api/samples").get_json()
    assert listing["total"] == 4
    assert listing["companies"][0] == {"name": "甲公司", "count": 2}
    assert {item["company"] for item in listing["items"]} == {"甲公司", "乙公司", "未分类"}
    assert all(item["status"] == "未测试" for item in listing["items"])
    for suffix in ("", "?thumbnail=1"):
        response = client.get("/api/samples/image/one.png" + suffix)
        assert response.status_code == 200
        assert Image.open(BytesIO(response.data)).size == (60, 90)
    assert client.get("/api/samples/image/missing.png").status_code == 404
    assert client.get("/api/samples/image/symlink.png").status_code == 404
    assert client.get("/files/samples/original/../outside.png").status_code == 404
    assert client.get("/api/samples/history").get_json() == []
    assert ctx.database.list_results() == []
    assert ctx.database.list_tasks() == []


def test_batch_is_atomic_deduplicated_and_separate_from_production(samples):
    ctx, client = samples
    assert client.post("/api/samples/run", json={"filenames": ["one.png", "missing.png"]}).status_code == 400
    assert client.get("/api/samples/history").get_json() == []
    response = client.post("/api/samples/run", json={"filenames": ["one.png", "two.png", "one.png"]})
    assert response.status_code == 202
    assert response.json["accepted"] == ["one.png", "two.png"]
    assert response.json["skipped"] == []
    response = client.post("/api/samples/run", json={"filenames": ["one.png", "three.png"]})
    assert response.json["accepted"] == ["three.png"]
    assert response.json["skipped"] == ["one.png"]
    states = {row["filename"]: row["status"] for row in client.get("/api/samples").json["items"]}
    assert states == {"one.png": "测试中", "two.png": "测试中", "three.png": "测试中", "blank.png": "未测试"}
    assert ctx.database.list_tasks() == []
    assert ctx.database.list_results() == []
    assert len(client.get("/api/samples/history").json) == 3


def test_worker_results_images_and_history_remain_isolated(samples):
    ctx, client = samples
    # Same numeric id exists in production; the sample endpoint must not read it.
    production = ctx.database.insert_result(filename="production.png", stored_name="production.png",
        preview_name="", task_id="", result={"fields": {"客户名称": "正式客户"}, "overall": "通过"})
    client.post("/api/samples/run", json={"filenames": ["one.png"]})
    service = SampleCenter(ctx)
    job = service.store.claim()
    result, preview = service.worker.execute(job)
    assert result["preview_url"].startswith("/files/samples/previews/")
    assert result["processing_artifacts"]["date"][0]["original_url"].startswith("/files/samples/artifacts/")
    service.store.finish(job, result, preview)
    item = client.get("/api/samples").json["items"][0]
    assert item["status"] == "已完成"
    assert item["result_id"] == production
    detail = client.get(f"/api/samples/results/{item['result_id']}").json
    assert detail["sample_center"] is True
    assert detail["fields"]["客户名称"] == "甲公司"
    assert ctx.database.get_result(production)["fields"]["客户名称"] == "正式客户"
    assert client.get(detail["preview_url"]).status_code == 200
    assert client.get(detail["processing_artifacts"]["date"][0]["original_url"]).status_code == 200
    assert client.get(f"/api/samples/results/{production}/ground-truth").json["exists"]
    assert client.get(f"/api/samples/results/{production}/review-history").json == []
    assert client.patch(f"/api/samples/results/{production}/review", json={}).status_code == 404
    # A rerun is a new sample-only history entry.
    assert client.post("/api/samples/run", json={"filenames": ["one.png"]}).json["accepted"] == ["one.png"]
    assert len(client.get("/api/samples/history").json) == 2


def test_failures_keep_source_preview_and_can_be_retried(samples):
    ctx, client = samples
    client.post("/api/samples/run", json={"filenames": ["one.png"]})
    service = SampleCenter(ctx)
    job = service.store.claim()
    service.store.finish(job, error="测试识别失败")
    item = client.get("/api/samples").json["items"][0]
    assert item["status"] == "运行失败"
    assert item["error"] == "测试识别失败"
    detail = client.get(f"/api/samples/results/{item['result_id']}").json
    assert client.get(detail["preview_url"]).status_code == 200
    assert client.post("/api/samples/run", json={"filenames": ["one.png"]}).json["accepted"] == ["one.png"]


def test_pending_and_recovered_tests_retain_their_own_configuration(samples):
    ctx, client = samples
    first = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    second = deepcopy(first)
    second["recognition_config"]["products"] = ["paddle_v6"]
    ctx.database.set_json_setting("recognition_defaults", first)
    client.post("/api/samples/run", json={"filenames": ["one.png"]})
    ctx.database.set_json_setting("recognition_defaults", second)
    client.post("/api/samples/run", json={"filenames": ["two.png"]})
    service = SampleCenter(ctx)
    job = service.store.claim()
    assert json.loads(job["payload_json"])["recognition_config"]["products"] == []
    service.store.recover()
    job = service.store.claim()
    assert json.loads(job["payload_json"])["recognition_config"]["products"] == []
    service.store.finish(job, error="stub")
    other = service.store.claim()
    assert json.loads(other["payload_json"])["recognition_config"]["products"] == ["paddle_v6"]


@pytest.mark.parametrize("payload", [None, [], {}, {"filenames": []}, {"filenames": [3]},
                                     {"filenames": ["../outside.png"]}])
def test_invalid_requests_do_not_create_tasks(samples, payload):
    _, client = samples
    assert client.post("/api/samples/run", json=payload).status_code == 400
    assert client.get("/api/samples/history").json == []
