from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask
import pytest

from receipt_ocr.persistence.database import Database
from receipt_ocr.recognition.seal.postprocess.name_correction import company_name_terms
from receipt_ocr.web.seal_recognition_info import standard_recognition_info

COMPANY = '深圳欧瑞特供应链管理有限公司'
NAME = COMPANY + '兰州分公司'
REQUIREMENT = NAME + '收货专用章（1）'


def save_standard(db, requirement=REQUIREMENT):
    return db.save_seal_standard(dict(requirement=requirement, image_name='stamp.png',
        recognized='供座链', shape='round', color='red', source_result_id=1,
        source_filename='source.jpg', candidate_id='seal-0'), expected_revision=0)


def add_history(db, *, requirement=REQUIREMENT, name=NAME, raw=None, date='2026-10-26T10:00:00'):
    correction = dict(raw_text=raw or name.replace('供应链', '供座链'), corrected_text=name,
        changes=[dict(rule='known_ocr_word', before='供座链', after='供应链')])
    return db.insert_result(filename=f'真实来源-{uuid4().hex}.jpg', stored_name='x.jpg', preview_name='x.jpg', task_id='',
        result=dict(created_at=date, fields={'签章要求':requirement},
            processing_artifacts={'seals':[dict(index=0, name_corrections=[correction])] }))


@pytest.fixture
def setup_info(tmp_path):
    db = Database(tmp_path/'results.db'); db.initialize()
    standard = save_standard(db)
    sample = Database(tmp_path/'sample-center'/'results.db'); sample.initialize()
    app = Flask(__name__)
    ctx = SimpleNamespace(database=db)
    app.add_url_rule('/info/<int:id>', view_func=lambda id: standard_recognition_info(ctx, id))
    return db, sample, standard, app.test_client()


def test_terms_preserve_company_branch_purpose_and_number():
    terms = company_name_terms(REQUIREMENT)
    assert {key:terms[key] for key in ('company','branch','purpose','number','supported')} == dict(
        company=COMPANY, branch='兰州分公司', purpose='收货专用章', number='(1)', supported=True)
    terms = company_name_terms('三星（中国）投资有限公司公章（2）')
    assert terms['company'] == '三星(中国)投资有限公司'
    assert terms['number'] == '(2)'
    assert not company_name_terms('客户章')['supported']


def test_real_terms_share_registered_siblings_and_empty_history_is_honest(setup_info):
    db, _, standard, client = setup_info
    save_standard(db, COMPANY+'武汉分公司收货专用章（1）')
    save_standard(db, COMPANY+'兰州分公司业务章（2）')
    save_standard(db, '不同有限公司北京分公司公章')
    data = client.get(f'/info/{standard["id"]}').get_json()
    assert data['terms']['company'] == COMPANY
    assert data['branches'] == ['兰州分公司', '武汉分公司']
    assert data['history'] == [] and not data['has_more']
    assert [rule['key'] for rule in data['rules']] == ['company_name', 'edge_noise', 'number']
    assert client.get('/info/99999').status_code == 404


def test_history_sources_numbers_deletion_and_readonly(setup_info):
    db, sample, standard, client = setup_info
    receipt_id = add_history(db)
    sample_id = add_history(sample, date='2026-10-26T11:00:00')
    deleted = add_history(db); db.delete_results([deleted])
    add_history(db, requirement=NAME+'收货专用章（2）')
    add_history(db, name=COMPANY+'武汉分公司')
    before = deepcopy(db.get_result(receipt_id))
    data = client.get(f'/info/{standard["id"]}').get_json()
    assert [(item['source_kind'], item['result_id']) for item in data['history']] == [
        ('sample',sample_id), ('receipt',receipt_id)]
    assert data['history'][0]['raw_text'] == NAME.replace('供应链','供座链')
    assert data['history'][0]['corrected_text'] == NAME
    assert db.get_result(receipt_id) == before


def test_history_respects_reviewed_requirement_and_limits(setup_info):
    db, _, standard, client = setup_info
    for _ in range(23): add_history(db)
    other = add_history(db)
    with db.connect() as connection:
        connection.execute('UPDATE results SET summary_json=? WHERE id=?',
            ('{"page_review_override":{"fields":{"签章要求":"其他要求"}}}', other))
    data = client.get(f'/info/{standard["id"]}').get_json()
    assert len(data['history']) == 20 and data['has_more']
    assert other not in [item['result_id'] for item in data['history']]


def test_production_route_is_registered():
    from receipt_ocr.web.application import app
    assert any(rule.endpoint == 'seal_standard_recognition_info' and 'GET' in rule.methods
               for rule in app.url_map.iter_rules())


def test_archive_list_and_info_share_the_same_terms_and_preserve_stored_text(setup_info):
    from flask import Flask
    from receipt_ocr.web.seal_standards import list_standards
    db,_,standard,client=setup_info
    saved=save_standard(db,'华润万象汇三星授权体验店')
    app=Flask(__name__)
    app.add_url_rule('/standards',view_func=lambda:list_standards(SimpleNamespace(database=db)))
    response=app.test_client().get('/standards').get_json()['items']
    item=next(s for s in response if s['id']==saved['id'])
    info=client.get(f'/info/{saved["id"]}').get_json()
    assert item['name_terms']==info['terms']
    assert item['requirement']==saved['requirement']
    assert 'name_terms' not in db.get_seal_standard(saved['requirement'])
