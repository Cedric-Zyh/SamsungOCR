"""Preview a queued receipt without running OCR or confusing same-name files."""
from io import BytesIO

import pytest
from flask import Flask
from PIL import Image

from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from receipt_ocr.web.dependencies import QueueDependencies
from receipt_ocr.web.queue import job_image


@pytest.fixture
def preview(tmp_path):
    database = Database(tmp_path / "preview.db")
    database.initialize()
    store = JobStore(database)
    store.initialize()
    uploads, samples = tmp_path / "uploads", tmp_path / "samples"
    uploads.mkdir()
    samples.mkdir()
    ctx = QueueDependencies({".jpg", ".png"}, samples, uploads, False, database, store, None)
    app = Flask(__name__)
    app.add_url_rule("/api/jobs/<job_id>/image", view_func=lambda job_id: job_image(ctx, job_id))
    jobs = store.create_batch("preview", "预览测试",
        [{"filename": "same.png"}, {"filename": "same.png"}],
        {"ocr_backend": "paddle_v6", "seal_recognition_mode": "local"})["items"]
    return ctx, jobs, app.test_client()


def test_preview_uses_job_identity_without_starting_or_saving_results(preview):
    ctx, jobs, client = preview
    for index, color in enumerate(("red", "blue")):
        name = f"{index}.png"
        Image.new("RGB", (20, 30), color).save(ctx.upload_dir / name)
        ctx.job_store.accept_upload(jobs[index]["id"], name)
    before = [ctx.job_store.get(job["id"]) for job in jobs]
    for index, color in enumerate(((255, 0, 0), (0, 0, 255))):
        response = client.get(f'/api/jobs/{jobs[index]["id"]}/image')
        assert response.status_code == 200
        assert Image.open(BytesIO(response.data)).getpixel((0, 0)) == color
    assert [ctx.job_store.get(job["id"]) for job in jobs] == before
    with ctx.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0


def test_missing_upload_does_not_fall_back_to_same_named_sample(preview):
    ctx, jobs, client = preview
    Image.new("RGB", (20, 30)).save(ctx.data_dir / "same.png")
    job_id = jobs[0]["id"]
    assert client.get(f"/api/jobs/{job_id}/image").status_code == 404
    ctx.job_store.accept_upload(job_id, "missing.png")
    assert client.get(f"/api/jobs/{job_id}/image").status_code == 404
    assert client.get("/api/jobs/unknown/image").status_code == 404


def test_sample_preview_is_safe_and_cancelled_jobs_are_unavailable(preview, tmp_path):
    ctx, jobs, client = preview
    Image.new("RGB", (20, 30)).save(ctx.data_dir / "same.png")
    job_id = jobs[0]["id"]
    ctx.job_store.accept_upload(job_id, "sample:same.png")
    assert client.get(f"/api/jobs/{job_id}/image").status_code == 200
    Image.new("RGB", (20, 30)).save(tmp_path / "outside.png")
    with ctx.database.connect() as connection:
        connection.execute("UPDATE recognition_jobs SET stored_name=? WHERE id=?",
                           ("sample:../outside.png", job_id))
    assert client.get(f"/api/jobs/{job_id}/image").status_code == 404
    with ctx.database.connect() as connection:
        connection.execute("UPDATE recognition_jobs SET stored_name=?,status='cancelled' WHERE id=?",
                           ("sample:same.png", job_id))
    assert client.get(f"/api/jobs/{job_id}/image").status_code == 404
