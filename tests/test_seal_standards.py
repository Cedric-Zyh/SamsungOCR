from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from receipt_ocr.domain.seal_standards import apply_standard_check, seal_candidates, requirement_key
from receipt_ocr.persistence.database import Database
from receipt_ocr.web import application as web

REQUIREMENT = "深圳测试有限公司收货专用章（1）"


def artifact(index=0, text=REQUIREMENT, shape="round", color="red"):
    return {"index": index, "region_id": f"seal-{index}", "shape_code": shape, "color": color,
            "original_url": f"/files/artifacts/test/{index}-original.png",
            "color_isolated_url": f"/files/artifacts/test/{index}-color.png",
            "color_isolated_oriented_url": f"/files/artifacts/test/{index}-oriented.png",
            "reads": [{"channel": "body", "text": text, "confidence": .98}]}


def receipt(*artifacts):
    return {"fields": {"签章要求": REQUIREMENT, "要求到货": "2026-10-12"},
            "date_check": {"actual": "2026-10-12", "status": "匹配", "reliable": True},
            "seal_check": {"recognized": REQUIREMENT, "status": "匹配", "reliable": True},
            "processing_artifacts": {"seals": list(artifacts)}, "review_issues": [], "review_reasons": [],
            "overall": "通过", "final_result": "通过", "review_status": "待复核"}


def standard(shape="round"):
    return {"id": 1, "shape": shape, "color": "red", "revision": 1, "requirement": REQUIREMENT}


def test_any_one_stamp_satisfies_both_conditions_without_requiring_all():
    result = apply_standard_check(receipt(artifact(0, "其他客户章", "rectangle"), artifact(1)), standard())
    assert result["seal_check"]["status"] == "匹配"
    assert result["overall"] == "通过"
    assert [c["matched"] for c in result["seal_standard_check"]["candidates"]] == [False, True]


def test_never_pair_text_from_one_stamp_with_shape_from_another():
    result = apply_standard_check(receipt(artifact(0, REQUIREMENT, "rectangle"), artifact(1, "其他客户章")), standard())
    assert result["seal_check"]["status"] == "不匹配"
    assert result["overall"] != "通过"


def test_never_combine_ring_from_one_stamp_with_center_from_another():
    a, b = artifact(0, ""), artifact(1, "")
    a["unwrapped_text"] = "深圳测试有限公司"
    b["round_type_band_text"] = "收货专用章（1）"
    assert apply_standard_check(receipt(a, b), standard())["seal_standard_check"]["status"] == "unmatched"


def test_missing_reference_keeps_text_evidence_and_never_autoregisters(tmp_path):
    db = Database(tmp_path / "results.db"); db.initialize()
    original = receipt(artifact())
    result = apply_standard_check(original, db.get_seal_standard(REQUIREMENT))
    assert result["seal_standard_check"]["status"] == "missing"
    assert result["seal_standard_check"]["candidates"][0]["shape_status"] == "尚无参照"
    assert result["seal_check"] == original["seal_check"]
    assert result["overall"] == "需人工复核"
    assert db.list_seal_standards() == []


def test_color_difference_is_visible_but_does_not_reject_a_legal_stamp():
    result = apply_standard_check(receipt(artifact(color="blue")), standard())
    assert result["seal_standard_check"]["candidates"][0]["color_status"] == "不同"
    assert result["overall"] == "通过"


def test_unknown_shape_and_misspelled_company_cannot_auto_match():
    for candidate in [artifact(shape=""), artifact(text=REQUIREMENT.replace("测试", "测式"))]:
        assert apply_standard_check(receipt(candidate), standard())["overall"] != "通过"


def test_manual_judgement_and_other_checks_are_preserved():
    item = receipt(artifact());item["seal_check"]["human_confirmed_match"] = False
    item["seal_check"].update(status="不匹配", reliable=True)
    assert apply_standard_check(item, standard())["seal_check"]["status"] == "不匹配"
    item=receipt(artifact());item["date_check"].update(status="不匹配")
    assert apply_standard_check(item, standard())["overall"] != "通过"


def test_requirement_key_keeps_different_station_numbers_and_chinese_names_distinct():
    assert requirement_key("深圳 测试（１）") == requirement_key("深圳测试(1)")
    assert requirement_key("深圳测试（1）") != requirement_key("深圳测试（2）")


@pytest.fixture
def app_store(tmp_path, monkeypatch):
    db=Database(tmp_path/'results.db');db.initialize()
    artifact_dir=tmp_path/'artifacts';folder=artifact_dir/'test';folder.mkdir(parents=True)
    for index in [0,1]:
        for name,color in [('original','black'),('color','pink'),('oriented','red')]:
            Image.new('RGB',(40,24),color).save(folder/f'{index}-{name}.png')
    monkeypatch.setattr(web,'database',db)
    monkeypatch.setattr(web,'ARTIFACT_DIR',artifact_dir)
    client=web.app.test_client()
    def add(*candidates, requirement=REQUIREMENT):
        item=receipt(*candidates);item['fields']['签章要求']=requirement
        return db.insert_result(filename='回单.png',stored_name='sample:test.png',preview_name='test.png',task_id='',result=item)
    return db,client,add,folder


def register(client, result_id, candidate_index=0, revision=0, **extra):
    detail=client.get(f'/api/results/{result_id}').get_json()
    payload={'candidate_id':detail['seal_standard_check']['candidates'][candidate_index]['id'],
             'review_revision':detail['review_revision'],'standard_revision':revision,**extra}
    return client.post(f'/api/results/{result_id}/seal-standard',json=payload)


def test_register_uses_clean_oriented_png_and_survives_receipt_artifact_removal(app_store):
    db,client,add,folder=app_store
    result_id=add(artifact())
    assert client.get('/api/seal-standards').get_json()['items']==[]
    response=register(client,result_id)
    assert response.status_code==200,response.get_json()
    saved=response.get_json()['standard']
    assert saved['recognized']==REQUIREMENT
    image=client.get(saved['image_url']);assert image.status_code==200
    with Image.open(db.path.parent/'seal-standards'/Path(saved['image_url']).name) as im:
        assert im.getpixel((0,0))==(255,0,0)
    for p in folder.iterdir():p.unlink()
    db.delete_results([result_id])
    assert client.get(saved['image_url']).status_code==200
    assert len(db.list_seal_standards())==1


def test_replacement_updates_one_row_and_all_metadata_atomically(app_store):
    db,client,add,_=app_store
    first=register(client,add(artifact())).get_json()['standard']
    next_id=add(artifact(1,'实际识别文字','rectangle','blue'))
    response=register(client,next_id,revision=first['revision'],rotation=90)
    assert response.status_code==200,response.get_json()
    saved=response.get_json()['standard']
    assert saved['id']==first['id'] and saved['revision']==2
    assert saved['image_url']!=first['image_url']
    assert (saved['recognized'],saved['shape'],saved['color'])==('实际识别文字','rectangle','blue')
    assert len(db.list_seal_standards())==1
    with Image.open(db.path.parent/'seal-standards'/Path(saved['image_url']).name) as im:
        assert im.size==(24,40)
    assert register(client,next_id,revision=0).status_code==409
    assert len(db.list_seal_standards())==1


def test_old_form_cannot_overwrite_a_new_standard(app_store):
    _,client,add,_=app_store
    result_id=add(artifact())
    before=client.get(f'/api/results/{result_id}').get_json()
    assert register(client,result_id).status_code==200
    response=client.post(f'/api/results/{result_id}/seal-standard',json={
        'candidate_id':before['seal_standard_check']['candidates'][0]['id'],
        'review_revision':before['review_revision'],'standard_revision':0})
    assert response.status_code==409


def test_raw_crop_only_and_path_escape_cannot_be_registered(app_store):
    db,client,add,_=app_store
    raw=artifact();raw.pop('color_isolated_url');raw.pop('color_isolated_oriented_url')
    assert register(client,add(raw)).status_code==400
    escaping=artifact();escaping['color_isolated_oriented_url']='/files/artifacts/../../secret.png'
    assert register(client,add(escaping)).status_code==400
    assert db.list_seal_standards()==[]


def test_center_replacement_sources_are_limited_to_same_requirement(app_store):
    _,client,add,_=app_store
    registered=add(artifact());same=add(artifact(1));different=add(artifact(),requirement='其他客户章')
    saved=register(client,registered).get_json()['standard']
    response=client.get(f"/api/seal-standards/{saved['id']}/sources")
    assert response.status_code==200
    ids={s['id'] for s in response.get_json()['sources']}
    assert ids=={registered,same} and different not in ids
    assert client.get('/api/seal-standards?search=测试').get_json()['items'][0]['id']==saved['id']


def test_getting_or_saving_review_draft_does_not_create_standard(app_store):
    db,client,add,_=app_store
    result_id=add(artifact())
    client.get(f'/api/results/{result_id}')
    response=client.patch(f'/api/results/{result_id}/review',json={'human_note':'稍后加入','review_status':'待复核'})
    assert response.status_code==200
    assert db.list_seal_standards()==[]


def test_requirement_edit_rechecks_new_standard_before_confirmation(app_store):
    db,client,add,_=app_store
    first=add(artifact())
    assert register(client,first).status_code==200
    response=client.patch(f'/api/results/{first}/review',json={
        'fields':{'签章要求':'不同公司收货章'},'seal_text':'不同公司收货章',
        'review_status':'确认通过','final_result':'通过'})
    assert response.status_code==409
    assert db.get_result(first)['fields']['签章要求']==REQUIREMENT
    response=client.patch(f'/api/results/{first}/review',json={
        'fields':{'签章要求':'不同公司收货章'},'review_status':'待复核','final_result':'需人工复核'})
    assert response.status_code==200
    assert response.get_json()['seal_standard_check']['status']=='missing'


def test_manual_confirmation_can_resolve_missing_standard_without_registering(app_store):
    db,client,add,_=app_store
    result_id=add(artifact())
    response=client.patch(f'/api/results/{result_id}/review',json={
        'seal_confirmed_match':True,'review_status':'确认通过','final_result':'通过'})
    assert response.status_code==200,response.get_json()
    assert response.get_json()['review_status']=='确认通过'
    assert db.list_seal_standards()==[]


def test_reopened_confirmed_receipt_cannot_transfer_judgement_to_new_requirement(app_store):
    _,client,add,_=app_store
    result_id=add(artifact())
    assert client.patch(f'/api/results/{result_id}/review',json={
        'seal_confirmed_match':True,'review_status':'确认通过','final_result':'通过'}).status_code==200
    response=client.patch(f'/api/results/{result_id}/review',json={
        'fields':{'签章要求':'其他章'},'seal_text':'其他章',
        'review_status':'确认通过','final_result':'通过'})
    assert response.status_code==409


def test_recognition_applies_existing_standard_before_result_is_saved(app_store, monkeypatch):
    _,client,add,_=app_store
    assert register(client,add(artifact())).status_code==200
    monkeypatch.setattr(web,'recognition_service',SimpleNamespace(
        recognize=lambda *args,**kwargs:receipt(artifact(shape='ellipse'))))
    result=web._configured_analyze('unused.png')
    assert result['seal_standard_check']['status']=='unmatched'
    assert result['overall']!='通过'


def test_file_is_cleaned_up_if_database_write_rolls_back(app_store,monkeypatch):
    db,client,add,_=app_store
    result_id=add(artifact())
    def fail(*args,**kwargs):
        raise ValueError('模拟写入失败')
    monkeypatch.setattr(db,'save_seal_standard',fail)
    assert register(client,result_id).status_code==400
    assert list((db.path.parent/'seal-standards').glob('*.png'))==[]
    assert db.list_seal_standards()==[]


def test_legacy_per_stamp_matching_texts_are_used_without_other_stamp_texts():
    candidate=artifact(text='识别片段')
    candidate['matching_texts']=[REQUIREMENT]
    assert apply_standard_check(receipt(candidate),standard())['overall']=='通过'


def test_reference_changes_refresh_pending_list_summaries_but_preserve_confirmed_reviews(app_store):
    db,client,add,_=app_store
    old=add(artifact());new=add(artifact(1,shape='ellipse'));confirmed=add(artifact())
    assert client.patch(f'/api/results/{old}/review',json={
        'human_note':'保留复核备注','error_type':'图像质量问题',
        'review_status':'待复核','final_result':'需人工复核'}).status_code==200
    assert client.patch(f'/api/results/{confirmed}/review',json={
        'seal_confirmed_match':True,'review_status':'确认通过','final_result':'通过'}).status_code==200
    assert register(client,old).status_code==200
    assert db.get_result(new)['overall']!='通过'
    assert register(client,new,revision=1).status_code==200
    assert db.get_result(old)['overall']!='通过'
    assert db.get_result(new)['overall']=='通过'
    assert db.get_result(old)['human_note']=='保留复核备注'
    assert db.get_result(old)['error_type']=='图像质量问题'
    assert db.get_result(confirmed)['overall']=='通过'
    assert db.get_result(confirmed)['review_status']=='确认通过'


def sample_standard_source():
    from receipt_ocr.web.samples import SampleCenter, sample_urls
    service = SampleCenter(web)
    folder = service.artifact_dir / "test"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 24), "blue").save(folder / "0-oriented.png")
    result_id = service.database.insert_result(
        filename="sample-only.png", stored_name="sample:sample-only.png",
        preview_name="", task_id="", result=sample_urls(receipt(artifact(color="blue"))))
    return service, result_id


def test_view_sample_can_register_shared_standard_without_using_same_id_production_record(app_store):
    db, client, add, _ = app_store
    production_id = add(artifact(), requirement="其他正式回单公司")
    before = db.get_result(production_id)
    service, sample_id = sample_standard_source()
    assert sample_id == production_id
    sample_before = service.database.get_result(sample_id)
    detail = client.get(f"/api/samples/results/{sample_id}").get_json()
    response = client.post(f"/api/samples/results/{sample_id}/seal-standard", json={
        "candidate_id": detail["seal_standard_check"]["candidates"][0]["id"],
        "review_revision": detail["review_revision"], "standard_revision": 0, "rotation": 90})
    assert response.status_code == 200, response.get_json()
    saved = response.get_json()
    assert saved["standard"]["source_kind"] == "sample"
    assert saved["standard"]["source_filename"] == "sample-only.png"
    assert saved["result"]["sample_center"] is True
    assert saved["result"]["seal_standard_check"]["status"] == "matched"
    assert saved["result"]["review_revision"] != detail["review_revision"]
    assert db.get_result(production_id) == before
    assert db.history(production_id) == []
    assert service.database.get_result(sample_id) == sample_before
    assert len(db.list_results()) == 1
    assert len(db.list_seal_standards()) == 1
    image = client.get(saved["standard"]["image_url"])
    assert image.status_code == 200
    from io import BytesIO
    with Image.open(BytesIO(image.data)) as stamp:
        assert stamp.size == (24, 40)
        assert stamp.getpixel((0, 0)) == (0, 0, 255)
    reopened = client.get(f"/api/samples/results/{sample_id}").get_json()
    assert reopened["seal_standard_check"]["standard"]["id"] == saved["standard"]["id"]


def test_sample_standard_replacement_rejects_stale_views_and_preserves_source_identity(app_store):
    db, client, add, _ = app_store
    saved = register(client, add(artifact())).get_json()["standard"]
    assert saved["source_kind"] == "receipt"
    _, sample_id = sample_standard_source()
    detail = client.get(f"/api/samples/results/{sample_id}").get_json()
    payload = {"candidate_id": detail["seal_standard_check"]["candidates"][0]["id"],
               "review_revision": detail["review_revision"], "standard_revision": saved["revision"]}
    endpoint = f"/api/samples/results/{sample_id}/seal-standard"
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200, response.get_json()
    replacement = response.get_json()["standard"]
    assert replacement["id"] == saved["id"]
    assert replacement["revision"] == 2
    assert replacement["source_kind"] == "sample"
    assert client.post(endpoint, json=payload).status_code == 409
    assert db.get_seal_standard(REQUIREMENT)["revision"] == 2
    assert db.history(sample_id)[0]["action"] == "标准章更新后重新核对"


def test_sample_registration_rejects_missing_artifacts_and_path_escape(app_store):
    db, client, _, _ = app_store
    service, sample_id = sample_standard_source()
    (service.artifact_dir / "test" / "0-oriented.png").unlink()
    detail = client.get(f"/api/samples/results/{sample_id}").get_json()
    endpoint = f"/api/samples/results/{sample_id}/seal-standard"
    payload = {"candidate_id": detail["seal_standard_check"]["candidates"][0]["id"],
               "review_revision": detail["review_revision"], "standard_revision": 0}
    assert client.post(endpoint, json=payload).status_code == 400
    escaping = artifact()
    escaping["color_isolated_oriented_url"] = "/files/samples/artifacts/../../outside.png"
    other = service.database.insert_result(filename="escape.png", stored_name="sample:escape.png",
        preview_name="", task_id="", result=receipt(escaping))
    detail = client.get(f"/api/samples/results/{other}").get_json()
    payload.update(candidate_id=detail["seal_standard_check"]["candidates"][0]["id"],
                   review_revision=detail["review_revision"])
    assert client.post(f"/api/samples/results/{other}/seal-standard", json=payload).status_code == 400
    assert db.list_seal_standards() == []


def test_picker_prefers_local_only_when_proven_to_reuse_remote_region():
    local = artifact()
    remote = {"index": 0, "api_index": 4, "source": "清瞳印章区域", "shape": "圆形", "color": "red",
              "original_url": "/files/artifacts/qingtong-0.png"}
    other = {**remote, "index": 1, "api_index": 7, "original_url": "/files/artifacts/qingtong-1.png"}
    item = receipt(local, remote, other)
    # Equal ordinal numbers alone never prove two crops describe the same stamp.
    assert not any(c["registration_hidden"] for c in seal_candidates(item))
    item["recognition_variants"] = {"seal": [{"details": {
        "seal_check": {"region_source": "清瞳印章区域"},
        "processing_artifacts": {"seals": [local]},
    }}]}
    candidates = seal_candidates(item)
    assert len(candidates) == 3  # Automatic comparisons still see all OCR evidence.
    assert [c["registration_hidden"] for c in candidates] == [False, True, False]
    assert candidates[0]["original_url"] == local["original_url"]
    # Incomplete local evidence must not suppress the remote fallback.
    local["shape_code"] = "unknown"
    assert not any(c["registration_hidden"] for c in seal_candidates(item))


def test_picker_never_borrows_provenance_from_another_local_crop():
    local = artifact()
    remote = {"index": 0, "source": "清瞳印章区域", "original_url": "/remote.png"}
    item = receipt(local, remote)
    item["recognition_variants"] = {"seal": [{"details": {
        "seal_check": {"region_source": "清瞳印章区域"},
        "processing_artifacts": {"seals": [{**local,"original_url":"/different-run.png"}]},
    }}]}
    assert not any(c["registration_hidden"] for c in seal_candidates(item))
