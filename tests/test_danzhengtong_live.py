"""Production flow tested with deterministic HTTP responses, never real uploads."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from receipt_ocr import danzhengtong as dzt
from receipt_ocr.recognition_config import run_configured


def settings(**kwargs):
    return dzt.Settings(app_id='id', app_key='key', app_secret='secret', key_id='gateway',
                        callback_url='http://example.com/notify', **kwargs)


def response(body):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


def commit():
    return {name: {'value': value, 'ratio': 1, 'position': []} for name, value in {
        '客户名称':'测试有限公司', '仓库联系人':'联系人', '仓库接收人':'接收人',
        '签章要求':'测试有限公司售后专用章', '收货客户印章':'测试有限公司 售后专用章',
        '要求到货':'2025-08-27', '签收日期':'2025-08-27', '合计数量':1,
        '实收数量':'', '拒收数量':0}.items()}


def test_real_pipeline_uploads_once_polls_and_maps_all_stages(monkeypatch, tmp_path):
    calls, sleeps = [], []
    def request(method, url, **kwargs):
        calls.append((method, url))
        if 'files' in kwargs:
            return response({'status': True, 'fileId': 'uploaded'})
        if method == 'POST':
            assert kwargs['json']['docType'] == 'SINGLE_LLM_EXTRACT_ASYNC'
            assert kwargs['json']['modelId'] == 'logistics_samsung_deliverynote_1503'
            return response({'code':200,'status':True,'data':{'reqUuid':'uuid'}})
        return response({'code':200,'status':True,'data':{'commitResult':None if len(calls) == 3 else commit()}})
    client = dzt.Client(settings(), transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt, 'Client', lambda: client)
    monkeypatch.setattr(dzt.time, 'sleep', lambda seconds: sleeps.append(seconds))
    def forbidden(*a, **kw):
        raise AssertionError('local OCR called for remote-only plan')
    analyzer = SimpleNamespace(seal_api=SimpleNamespace(enabled=False), run_stage=forbidden)
    (tmp_path/'sample.jpg').write_bytes(b'sample')
    result = run_configured(analyzer, tmp_path/'sample.jpg', None,
        config={stage:['danzhengtong'] for stage in ('fields','handwriting','date','seal')})
    assert [x[0] for x in calls] == ['POST','POST','GET','GET']
    assert sleeps == [2,2]
    assert result['fields']['拒收数量'] == '0'
    assert result['fields']['实收数量'] == ''
    assert result['fields']['签收日期'] == '2025-08-27'
    assert '测试有限公司' in result['seal_check']['recognized']
    assert result['date_check']['status'] == '匹配'
    assert result['date_check']['reliable'] is True
    assert result['date_check']['source'] == '单证通'
    assert result['seal_check']['status'] == '匹配'
    assert result['seal_check']['reliable'] is True
    assert result['field_metadata']['客户名称']['provider_ratio'] == 1
    assert 'confidence' not in result['field_metadata']['客户名称']
    assert result['danzhengtong']['query']['attempts'] == 2
    assert not result['danzhengtong']['simulated']
    assert result['overall'] == '通过'
    assert '单证通日期/印章为文字提取结果，需人工核对原图' not in result['review_reasons']
    assert '仅单证通识别，文档版式待复核' not in result['review_reasons']
    assert result['document_type']['provider_fields_accepted'] is True
    assert result['document_type']['type'] == 'unclassified'
    assert result['document_type']['confidence'] == 0
    assert result['review_reasons'] == []


def cached_stage(stage, *, actual='2025-02-10', required='2025-02-10',
                 seal_text='太原市伊加壹电子服务总汇', requirement='太原市伊加壹电子服务总汇', simulated=False):
    from receipt_ocr.document_context import DocumentContext
    values = commit()
    values['签收日期']['value'] = actual
    values['收货客户印章']['value'] = seal_text
    cache = {'fixture': dzt.normalize_result(values), 'trace': {'simulated': simulated}}
    return dzt.stage_result(DocumentContext('unused.jpg'), stage, cache,
                            {'要求到货': required, '签章要求': requirement})


@pytest.mark.parametrize('trace,fields,accepted', [
    ({'mode':'real','simulated':False,'status':'completed'}, {'客户名称':'测试客户'}, True),
    ({'mode':'mock','simulated':True,'status':'completed'}, {'客户名称':'模拟客户'}, False),
    ({'mode':'real','simulated':False,'status':'failed'}, {'客户名称':'测试客户'}, False),
    ({'mode':'real','simulated':False}, {'客户名称':'测试客户'}, False),
    ({'mode':'real','simulated':False,'status':'completed'}, {'客户名称':'  ', '合计数量':None}, False),
])
def test_field_acceptance_requires_completed_real_nonempty_extraction(trace, fields, accepted):
    from copy import deepcopy
    from receipt_ocr.provider_field_policy import accept_real_dzt_fields
    document = {'type':'unclassified', 'reliable':False, 'confidence':0,
                'reasons':['仅单证通识别，文档版式待复核', '保留其他原因']}
    original = deepcopy(document)
    updated = accept_real_dzt_fields(document, fields=fields, trace=trace)
    assert document == original
    assert bool(updated.get('provider_fields_accepted')) is accepted
    assert updated['reliable'] is False and updated['confidence'] == 0
    assert updated['reasons'] == (['保留其他原因'] if accepted else original['reasons'])


def test_accepting_fields_does_not_override_known_document_routing():
    from receipt_ocr.provider_field_policy import accept_real_dzt_fields
    document = {'type':'warehouse_authorization', 'reliable':True, 'confidence':.99,
                'reasons':['仓库货物接收委托书不适用回单日期/印章模板']}
    updated = accept_real_dzt_fields(document, fields={'客户名称':'测试客户'},
        trace={'mode':'real','simulated':False,'status':'completed'})
    assert all(updated[key] == value for key, value in document.items())


def test_date_only_provider_does_not_claim_fields_were_accepted():
    from receipt_ocr.document_context import DocumentContext
    cache = {'fixture':dzt.normalize_result(commit()),
             'trace':{'mode':'real','simulated':False,'status':'completed'}}
    result = dzt.stage_result(DocumentContext('unused.jpg'), 'date', cache, {'要求到货':'2025-08-27'})
    assert not result['document_type'].get('provider_fields_accepted')
    assert result['document_type']['reasons'] == ['仅单证通识别，文档版式待复核']


@pytest.mark.parametrize('actual,required,status,reliable', [
    ('2025-02-10', '2025-02-10', '匹配', True),
    ('2025年2月10日', '2025-02-10', '匹配', True),
    ('2025-02-11', '2025-02-10', '不匹配', True),
    ('', '2025-02-10', '未识别', False),
    ('2025-02-30', '2025-02-10', '未识别', False),
    ('2025-02-10', '', '无法判断', False),
])
def test_provider_date_uses_returned_value_and_names_source(actual, required, status, reliable):
    check = cached_stage('date', actual=actual, required=required)['date_check']
    assert (check['status'], check['reliable']) == (status, reliable)
    assert check['source'] == check['backend'] == '单证通'
    assert check['raw_text'] == actual
    assert 'confidence' not in check


@pytest.mark.parametrize('stage', ['date', 'seal'])
def test_simulated_provider_match_stays_unconfirmed(stage):
    result = cached_stage(stage, simulated=True)
    check = result[f'{stage}_check']
    assert check['status'] == '匹配'
    assert check['reliable'] is False
    assert check['source'] == '单证通（模拟）'
    assert dzt.MOCK_NOTICE in result['stage_review_reasons']


@pytest.mark.parametrize('text,requirement,status', [
    ('太原市伊服壹电子服务总汇', '太原市伊加壹电子服务总汇', '不匹配'),
    ('测试（上海）有限公司', '测试(上海)有限公司', '匹配'),
    ('测试（上海）有限公司', '测试(北京)有限公司', '不匹配'),
    ('', '测试有限公司', '未识别'),
])
def test_provider_seal_compares_characters_strictly(text, requirement, status):
    check = cached_stage('seal', seal_text=text, requirement=requirement)['seal_check']
    assert check['recognized'] == text
    assert check['status'] == status
    assert check['source'] == '单证通'
    assert check['reliable'] is bool(text)


def test_editing_provider_seal_cannot_turn_one_character_difference_into_match():
    from receipt_ocr.review import apply_human_edits
    record = cached_stage('seal')
    record['fields'] = {'签章要求': '太原市伊加壹电子服务总汇'}
    edited = apply_human_edits(record, {'seal_text': '太原市伊服壹电子服务总汇'})
    assert edited['seal_check']['status'] == '不匹配'


def test_provider_seal_omissions_are_partial_and_do_not_pass_after_text_edit():
    from receipt_ocr.review import apply_human_edits
    result = cached_stage('seal', seal_text='太原市伊加壹电子服务')
    assert result['seal_check']['status'] == '部分匹配'
    assert result['seal_check']['reliable'] is False
    record = cached_stage('seal')
    record['fields'] = {'签章要求': '太原市伊加壹电子服务总汇'}
    edited = apply_human_edits(record, {'seal_text': '太原市伊加壹电子服务'})
    assert edited['seal_check']['status'] == '部分匹配'
    assert edited['seal_check']['reliable'] is False


def test_timeout_keeps_uuid_without_resubmitting(monkeypatch, tmp_path):
    calls, elapsed = [], [0]
    def request(method, url, **kwargs):
        calls.append(method)
        if 'files' in kwargs: return response({'status':True,'fileId':'uploaded'})
        if method == 'POST': return response({'code':200,'status':True,'data':{'reqUuid':'timeout-uuid'}})
        return response({'code':200,'status':True,'data':{'commitResult':None}})
    monkeypatch.setattr(dzt.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(dzt.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0]+seconds))
    source = tmp_path/'sample.jpg'; source.write_bytes(b'sample')
    client = dzt.Client(settings(poll_timeout_seconds=4), transport=SimpleNamespace(request=request))
    with pytest.raises(RuntimeError, match='timeout-uuid'):
        client.recognize(source)
    assert calls == ['POST','POST','GET']  # No new query at the deadline.
    assert client.jobs['timeout-uuid']['status'] == 'failed'


def test_stage_failure_cached_without_retrying_upload(monkeypatch, tmp_path):
    calls = []
    def recognize(*a, **kw):
        calls.append(1)
        raise RuntimeError('upload failed')
    monkeypatch.setattr(dzt, 'Client', lambda: SimpleNamespace(recognize=recognize))
    context = SimpleNamespace(source=tmp_path/'sample.jpg', filename='sample.jpg')
    cache = {}
    for stage in ('fields','date','seal'):
        with pytest.raises(RuntimeError, match='upload failed'):
            dzt.stage_result(context, stage, cache)
    assert len(calls) == 1


def test_default_http_session_ignores_proxy_environment(monkeypatch, tmp_path):
    import requests
    sessions = []
    class Session:
        trust_env = True
        def request(self, *a, **kw):
            assert self.trust_env is False
            return response({'status': True, 'fileId': 'uploaded'})
    def factory():
        instance = Session(); sessions.append(instance); return instance
    monkeypatch.setattr(requests, 'Session', factory)
    monkeypatch.setenv('HTTP_PROXY', 'http://invalid.example:9999')
    source=tmp_path/'sample.jpg'; source.write_bytes(b'sample')
    assert dzt.Client(settings()).upload_file(source)['fileId'] == 'uploaded'
    assert len(sessions) == 1


def test_recognition_serializes_documents(monkeypatch):
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def recognize_mock(self, source, **kwargs):
        calls.append(source)
        if source == 'first':
            entered.set()
            assert release.wait(3)
        return {}, {}
    monkeypatch.setattr(dzt.Client, 'recognize_mock', recognize_mock)
    first, second = dzt.Client(dzt.Settings(mode='mock')), dzt.Client(dzt.Settings(mode='mock'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first.recognize, 'first')
        assert entered.wait(2)
        def start_second():
            second_started.set()
            return second.recognize('second')
        two = pool.submit(start_second)
        assert second_started.wait(2)
        assert calls == ['first']
        release.set()
        one.result(2); two.result(2)
    assert calls == ['first','second']
