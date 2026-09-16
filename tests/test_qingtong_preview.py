from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

import app as web
from receipt_ocr.database import Database
from receipt_ocr.qingtong_preview import render_selected_seal, selected_seal_xyxy
from receipt_ocr.qingtong_seal import compare_qingtong_seal


def check(box=(130, 40, 180, 90)):
    selected = {"index": 1, "xyxy": list(box), "ocr": {"text": "判定使用的印章"}}
    return {"recognized": "判定使用的印章", "status": "匹配", "reliable": True,
            "api": {"ok": True}, "dual_check": {"policy": "qingtong_template_and_ocr",
            "selected": selected, "candidates": [{"index": 0, "xyxy": [20, 40, 70, 90]}, selected]}}


def image(path):
    picture = Image.new("RGB", (220, 130), "white")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((20, 40, 70, 90), fill="blue")
    draw.rectangle((130, 40, 180, 90), fill="red")
    picture.save(path)
    return path


def test_preview_crops_the_selected_stamp_and_preserves_original_colors(tmp_path):
    source = image(tmp_path / "stamps.png")
    evidence = check()
    before = deepcopy(evidence)
    crop = Image.open(render_selected_seal(source, evidence))
    assert crop.size == (66, 66)
    assert crop.getpixel((33, 33)) == (255, 0, 0)
    assert crop.getpixel((0, 0)) == (255, 255, 255)
    assert not any(rgb == (0, 0, 255) for rgb in crop.getdata())
    assert evidence == before


def test_new_any_channel_policy_crops_matching_template_selection(tmp_path):
    source = image(tmp_path / "stamps.png")
    requirement = '客户收货专用章'
    evidence = compare_qingtong_seal(requirement, {
        "ok": True,
        "response": {"data": {"img_0": [
            {"matched_seal": {"label": "错误公司"}, "text_formatted": "客户收货专用",
             "xyxy": [20, 40, 70, 90]},
            {"matched_seal": {"label": requirement}, "text_formatted": "错误公司",
             "xyxy": [130, 40, 180, 90]},
        ]}},
    })

    assert evidence['dual_check']['policy'] == 'qingtong_any_channel'
    assert evidence['dual_check']['selected']['index'] == 1
    assert selected_seal_xyxy(evidence) == (130, 40, 180, 90)
    crop = Image.open(render_selected_seal(source, evidence))
    assert crop.getpixel((33, 33)) == (255, 0, 0)


@pytest.mark.parametrize("box", [None, [], [1, 2, 3], [1, 2, 0, 4], [1, 4, 3, 2],
                                   [True, 0, 20, 20], ["1", 0, 20, 20],
                                   [0, 0, float("nan"), 20], [0, 0, float("inf"), 20]])
def test_missing_or_malformed_coordinates_are_not_usable(box):
    evidence = check()
    evidence["dual_check"]["selected"]["xyxy"] = box
    assert selected_seal_xyxy(evidence) is None


def test_absent_selection_never_falls_back_to_another_candidate():
    evidence = check()
    evidence["dual_check"]["selected"] = None
    assert selected_seal_xyxy(evidence) is None
    evidence = check()
    evidence["api"]["ok"] = False
    assert selected_seal_xyxy(evidence) is None


def test_clipping_to_image_bounds_and_rejecting_boxes_outside_it(tmp_path):
    source = image(tmp_path / "edge.png")
    crop = Image.open(render_selected_seal(source, check((-4, -3, 30.4, 40.1))))
    assert crop.size == (39, 49)
    with pytest.raises(ValueError):
        render_selected_seal(source, check((500, 500, 550, 550)))


def test_exif_orientation_uses_upright_coordinates(tmp_path):
    picture = Image.new("RGB", (220, 130), "white")
    ImageDraw.Draw(picture).rectangle((130, 40, 180, 90), fill="red")
    exif = picture.getexif()
    exif[274] = 6
    source = tmp_path / "rotated.jpg"
    picture.save(source, exif=exif, quality=100)
    crop = Image.open(render_selected_seal(source, check((39, 130, 89, 180))))
    red, green, blue = crop.getpixel((33, 33))
    assert red > 245 and green < 10 and blue < 10


@pytest.fixture
def preview_app(tmp_path, monkeypatch):
    database = Database(tmp_path / "preview.db")
    database.initialize()
    for setting in ("UPLOAD_DIR", "DATA_DIR"):
        directory = tmp_path / setting.lower()
        directory.mkdir()
        monkeypatch.setattr(web, setting, directory)
    monkeypatch.setattr(web, "database", database)
    def unexpected_remote(*args, **kwargs):
        raise AssertionError("Opening a preview must not call OCR")
    monkeypatch.setattr(web.analyzer.seal_api, "recognize", unexpected_remote)
    def insert(filename="receipt.png", evidence=None, *, kind="receipt", task="", stored=None):
        result = {"filename": filename, "fields": {"签章要求": "判定使用的印章"},
                  "overall": "需人工复核", "document_type": {"type": kind},
                  "seal_check": check() if evidence is None else evidence}
        return database.insert_result(filename=filename, stored_name=stored or filename,
                                      preview_name="", task_id=task, result=result)
    return web.app.test_client(), database, insert


def test_existing_record_preview_needs_no_rerun_or_database_write(preview_app):
    client, database, insert = preview_app
    image(web.UPLOAD_DIR / "receipt.png")
    record_id = insert()
    before = database.get_result(record_id)
    record = client.get(f"/api/results/{record_id}").get_json()
    response = client.get(f"/files/selected-seal/{record_id}.png?revision={record['review_revision']}")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert "no-store" in response.headers["Cache-Control"]
    assert Image.open(BytesIO(response.data)).getpixel((33, 33)) == (255, 0, 0)
    assert database.get_result(record_id) == before
    assert client.get(f"/files/selected-seal/{record_id}.png?revision=outdated").status_code == 409


def test_paginated_receipt_crops_the_page_that_supplied_the_decision(preview_app):
    client, database, insert = preview_app
    Image.new("RGB", (220, 130), "blue").save(web.UPLOAD_DIR / "receipt.png")
    image(web.UPLOAD_DIR / "receipt_01.png")
    cover_id = insert(evidence={"status": "未识别"}, task="batch")
    footer_id = insert("receipt_01.png", kind="product_continuation", task="batch")
    projected = client.get(f"/api/results/{cover_id}").get_json()
    assert projected["page_group"]["footer_result_id"] == footer_id
    response = client.get(f"/files/selected-seal/{cover_id}.png?revision={projected['review_revision']}")
    assert response.status_code == 200
    assert Image.open(BytesIO(response.data)).getpixel((33, 33)) == (255, 0, 0)


def test_sample_originals_are_supported(preview_app):
    client, _, insert = preview_app
    image(web.DATA_DIR / "sample.png")
    record_id = insert("sample.png", stored="sample:sample.png")
    assert client.get(f"/files/selected-seal/{record_id}.png").status_code == 200


def test_missing_deleted_and_invalid_preview_records_fail_cleanly(preview_app):
    client, database, insert = preview_app
    missing = insert()
    assert client.get(f"/files/selected-seal/{missing}.png").status_code == 404
    image(web.UPLOAD_DIR / "receipt.png")
    invalid = insert(evidence=check((500, 500, 550, 550)))
    assert client.get(f"/files/selected-seal/{invalid}.png").status_code == 404
    with database.connect() as connection:
        connection.execute("UPDATE results SET deleted_at='2026-09-10' WHERE id=?", (missing,))
    assert client.get(f"/files/selected-seal/{missing}.png").status_code == 404
    assert client.get("/files/selected-seal/999999.png").status_code == 404


def test_original_path_cannot_escape_storage(preview_app, tmp_path):
    client, _, insert = preview_app
    image(tmp_path / "outside.png")
    record_id = insert(stored="../outside.png")
    assert client.get(f"/files/selected-seal/{record_id}.png").status_code == 404
