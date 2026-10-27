"""Explicit layout settings persist independently of standard images/text."""
from copy import deepcopy

import pytest

from test_seal_standards import app_store, artifact, register
from receipt_ocr.recognition.seal.layout_settings import (
    explicit_horizontal_text_policy, seal_layout_settings_scope, seal_requirement_layout,
)


def test_existing_standard_is_unmarked_and_layout_patch_keeps_its_image_and_words(app_store):
    db, client, add, _ = app_store
    saved=register(client,add(artifact())).get_json()['standard']
    assert saved['horizontal_text']=='unmarked'
    response=client.patch(f"/api/seal-standards/{saved['id']}/layout",json={
        'horizontal_text':'absent','standard_revision':saved['revision']})
    assert response.status_code==200
    updated=response.get_json()['standard']
    assert updated['horizontal_text']=='absent' and updated['revision']==saved['revision']+1
    for key in ('image_url','recognized','ocr_recognized','shape','color','source_result_id','source_filename'):
        assert updated[key]==saved[key]
    assert db.get_seal_standard(saved['requirement'])['horizontal_text']=='absent'
    assert client.get('/api/seal-standards').get_json()['items'][0]['horizontal_text']=='absent'
    conflict=client.patch(f"/api/seal-standards/{saved['id']}/layout",json={
        'horizontal_text':'present','standard_revision':saved['revision']})
    assert conflict.status_code==409
    assert db.get_seal_standard(saved['requirement'])['horizontal_text']=='absent'


@pytest.mark.parametrize('value',['present','absent','unmarked'])
def test_round_and_ellipse_registration_accept_explicit_values(app_store,value):
    _,client,add,_=app_store
    for shape in ('round','ellipse'):
        ident=add(artifact(shape=shape),requirement='测试'+shape+'有限公司')
        saved=register(client,ident,horizontal_text=value).get_json()['standard']
        assert saved['horizontal_text']==value


def test_replacing_a_marked_standard_requires_a_fresh_choice(app_store):
    db,client,add,_=app_store
    ident=add(artifact())
    saved=register(client,ident,horizontal_text='absent').get_json()['standard']
    replacement=register(client,ident,revision=saved['revision']).get_json()['standard']
    assert replacement['horizontal_text']=='unmarked'
    assert db.get_seal_standard(saved['requirement'])['horizontal_text']=='unmarked'


@pytest.mark.parametrize('value',[None,True,False,1,{},[],'automatic','<img src=x>'])
def test_layout_endpoint_rejects_invalid_values_without_changes(app_store,value):
    db,client,add,_=app_store
    saved=register(client,add(artifact())).get_json()['standard']
    assert client.patch(f"/api/seal-standards/{saved['id']}/layout",json={
        'horizontal_text':value,'standard_revision':saved['revision']}).status_code==400
    assert db.get_seal_standard(saved['requirement'])['revision']==saved['revision']


def test_rectangle_has_no_layout_override_and_missing_standard_is_404(app_store):
    _,client,add,_=app_store
    saved=register(client,add(artifact(shape='rectangle'))).get_json()['standard']
    assert client.patch(f"/api/seal-standards/{saved['id']}/layout",json={
        'horizontal_text':'absent','standard_revision':saved['revision']}).status_code==400
    assert client.patch('/api/seal-standards/999999/layout',json={
        'horizontal_text':'absent','standard_revision':1}).status_code==404


def test_layout_choice_is_frozen_per_run_and_scoped_to_requirement_and_shape():
    source=[{'id':5,'revision':2,'requirement':'测试有限公司','shape':'round','horizontal_text':'absent'}]
    before=deepcopy(source)
    with seal_layout_settings_scope(source):
        source[0]['horizontal_text']='present'
        with seal_requirement_layout('测试 有限公司'):
            assert explicit_horizontal_text_policy('round')['setting']=='absent'
            assert explicit_horizontal_text_policy('ellipse') is None
            assert explicit_horizontal_text_policy('rectangle') is None
        with seal_requirement_layout('其他有限公司'):
            assert explicit_horizontal_text_policy('round') is None
        with seal_layout_settings_scope([dict(before[0],horizontal_text='present')]), seal_requirement_layout('测试有限公司'):
            assert explicit_horizontal_text_policy('round')['setting']=='present'
        with seal_requirement_layout('测试有限公司'):
            assert explicit_horizontal_text_policy('round')['setting']=='absent'
    with seal_requirement_layout('测试有限公司'):
        assert explicit_horizontal_text_policy('round') is None


def test_schema_upgrade_leaves_older_standard_unmarked(app_store):
    from receipt_ocr.persistence.seal_standards import initialize_seal_standards
    db,client,add,_=app_store
    saved=register(client,add(artifact())).get_json()['standard']
    with db.connect() as connection:
        connection.execute('ALTER TABLE seal_standards DROP COLUMN horizontal_text')
        initialize_seal_standards(connection)
    assert db.get_seal_standard(saved['requirement'])['horizontal_text']=='unmarked'


def test_web_recognition_uses_saved_layout_snapshot_before_running_the_recognizer(app_store,monkeypatch):
    from types import SimpleNamespace
    from receipt_ocr.web import application as web
    db,client,add,_=app_store
    ident=add(artifact())
    saved=register(client,ident,horizontal_text='absent').get_json()['standard']
    seen=[]
    def recognize(*args,**kwargs):
        with seal_requirement_layout(saved['requirement']):
            seen.append(explicit_horizontal_text_policy('round'))
        return db.get_result(ident)
    monkeypatch.setattr(web.remote_settings,'restore',lambda *args:None)
    monkeypatch.setattr(web,'recognition_service',SimpleNamespace(recognize=recognize))
    web._configured_analyze('unused-input')
    assert seen[0]['setting']=='absent' and seen[0]['standard_id']==saved['id']
    with seal_requirement_layout(saved['requirement']):
        assert explicit_horizontal_text_policy('round') is None
