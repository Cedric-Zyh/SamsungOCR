from stage_dependencies import patch_dependency
from types import SimpleNamespace
from copy import deepcopy
import pytest

from receipt_ocr import recognition_config as config_module
from receipt_ocr.recognition_config import run_configured, validate_config
from receipt_ocr.recognition_scope import provider_allowed, provider_scope


@pytest.fixture(autouse=True)
def available(monkeypatch):
    from receipt_ocr import document_context
    from receipt_ocr.ocr_types import TextObservation
    monkeypatch.setattr(document_context, 'recognize_text', lambda *a, **kw: [])
    monkeypatch.setattr(document_context, 'classify_document', lambda rows: {'type': 'receipt', 'reliable': True, 'confidence': .99})
    monkeypatch.setattr(document_context, '_find_signature_requirement_row', lambda rows: TextObservation('签章要求', .99, .1, .47, .2, .02))
    monkeypatch.setattr(config_module, 'backend_catalog', lambda: [{'id': x, 'available': True} for x in ['paddle', 'paddle_server', 'paddle_v6']])


def test_invalid_or_unavailable_plan():
    for plan in ({}, {'fields': []}, {'date': ['qingtong']}, {'fields': ['unknown']}):
        with pytest.raises(ValueError):
            validate_config(plan)
    with pytest.raises(ValueError):
        validate_config({'seal': ['qingtong']}, api_enabled=False)


@pytest.mark.parametrize('mode', ['none', 'polygon', 'doc_ori'])
def test_seal_orientation_selection_is_validated(mode):
    cleaned = validate_config({'seal': ['paddle'], 'seal_orientation': mode})
    assert cleaned['seal_orientation'] == mode
    with pytest.raises(ValueError, match='印章方向'):
        validate_config({'seal': ['paddle'], 'seal_orientation': 'unknown'})


@pytest.mark.parametrize('mode', ['none', 'polygon', 'doc_ori'])
def test_seal_orientation_reaches_stage_with_classifier_scope(mode):
    calls = []
    class Fake:
        seal_api = SimpleNamespace(enabled=True)

        def run_stage(self, context, stage, request):
            calls.append((request.seal_orientation_mode, provider_allowed('paddle_doc_ori')))
            return {'seal_check': {'recognized': '', 'status': '无法判断', 'reliable': False}}

    result = run_configured(Fake(), 'unused.jpg', None,
                            config={'seal': ['paddle'], 'seal_orientation': mode})
    assert calls == [(mode, mode == 'doc_ori')]
    assert result['seal_orientation_mode'] == mode


def test_multimethod_conflict_and_partial_scope(tmp_path):
    calls = []
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            preview = None
            kw = {'_targets': {stage}, '_route': request.route, '_previous_fields': request.fields,
                  'seal_recognition_mode': request.seal_mode}
            calls.append(kw)
            return {'fields': kw['_previous_fields'], 'date_check': {'actual': '2026-09-01' if kw['_route']['date'] == 'paddle_v6' else '2026-09-02', 'status': '匹配', 'reliable': True}, 'product_table': {}, 'field_metadata': {}}
    result = run_configured(Fake(), 'x', None, config={'date':['paddle_v6','paddle']}, previous_fields={'要求到货':'2026-09-01'}, ocr_backend='hybrid')
    assert [c['_targets'] for c in calls] == [{'date'}, {'date'}]
    assert result['seal_check']['status'] == '未执行'
    assert result['date_check']['reliable'] is False
    assert result['overall'] == '需人工复核'
    assert len(result['recognition_variants']['date']) == 2
    assert any('不一致' in r for r in result['review_reasons'])


@pytest.mark.parametrize('mode,expected_status,expected_overall', [
    ('any', '匹配', '通过'),
    ('all', '需人工复核', '需人工复核'),
])
def test_date_match_mode_uses_any_or_all_selected_results(mode, expected_status, expected_overall):
    class Fake:
        seal_api = SimpleNamespace(enabled=True)

        def run_stage(self, context, stage, request):
            actual = '2026-09-01' if request.route['date'] == 'paddle_v6' else '2026-09-02'
            return {'fields': {}, 'date_check': {
                'actual': actual, 'status': '匹配' if actual == '2026-09-01' else '不匹配', 'reliable': True,
            }}

    result = run_configured(Fake(), 'x', None, config={
        'date': ['paddle_v6', 'paddle'],
        'acceptance': {'date_match_mode': mode, 'seal_match_mode': 'none'},
    }, previous_fields={'要求到货': '2026-09-01'})
    assert result['date_check']['status'] == expected_status
    assert result['overall'] == expected_overall


def test_no_comparison_mode_skips_date_seal_and_signature_gates():
    class Fake:
        seal_api = SimpleNamespace(enabled=True)

        def run_stage(self, context, stage, request):
            return {'fields': {'仓库联系人': '甲'}, 'field_metadata': {}}

    result = run_configured(Fake(), 'x', None, config={
        'fields': ['paddle'],
        'acceptance': {'date_match_mode': 'none', 'seal_match_mode': 'none', 'signature_match_mode': 'none'},
    })
    assert result['signature_check']['status'] == '未核对'
    assert result['overall'] == '通过'


def test_default_rejection_standard_is_configurable_and_safe_by_default():
    from receipt_ocr.decision import decide_overall
    matched = {'status': '匹配', 'reliable': True}
    mismatched = {'status': '不匹配', 'reliable': True}
    assert decide_overall(mismatched, matched, [], {'reject_mode': 'none'}) == '需人工复核'
    assert decide_overall(mismatched, matched, [], {'reject_mode': 'any_mismatch'}) == '不通过'
    assert decide_overall(mismatched, matched, [], {'reject_mode': 'all_mismatch'}) == '需人工复核'
    assert decide_overall(mismatched, mismatched, [], {'reject_mode': 'all_mismatch'}) == '不通过'
    assert decide_overall(matched, matched, [], {
        'signature_match_mode': 'any', 'reject_mode': 'none'}, mismatched) == '需人工复核'
    assert decide_overall(matched, matched, [], {
        'signature_match_mode': 'any', 'reject_mode': 'any_mismatch'}, mismatched) == '不通过'


def test_low_confidence_fields_can_be_checked_or_ignored():
    from receipt_ocr.decision import decide_overall
    matched = {'status': '匹配', 'reliable': True}
    assert decide_overall(matched, matched, ['存在低置信度字段'],
                          {'low_confidence_mode': 'check'}) == '需人工复核'
    assert decide_overall(matched, matched, ['存在低置信度字段'],
                          {'low_confidence_mode': 'ignore'}) == '通过'
    assert decide_overall(matched, matched, ['关键字段低置信度：客户名称'],
                          {'low_confidence_mode': 'ignore'}) == '通过'


def test_low_confidence_mode_defaults_to_check_and_validates():
    plan = validate_config({'fields': ['paddle'], 'acceptance': {'low_confidence_mode': 'ignore'}})
    assert plan['acceptance']['low_confidence_mode'] == 'ignore'
    assert validate_config({'fields': ['paddle'], 'acceptance': {'low_confidence_mode': 'bad'}})['acceptance']['low_confidence_mode'] == 'check'


def test_date_and_seal_matches_can_pass_without_product_or_handwriting_rules():
    class Fake:
        seal_api = SimpleNamespace(enabled=True)

        def run_stage(self, context, stage, request):
            if stage == 'date':
                return {'fields': {}, 'date_check': {'actual': '2026-09-14', 'status': '匹配', 'reliable': True}}
            return {'fields': {}, 'seal_check': {'recognized': '客户收货章', 'status': '匹配', 'reliable': True}}

    result = run_configured(Fake(), 'x', None,
                            config={'date': ['paddle'], 'seal': ['paddle']},
                            previous_fields={'要求到货': '2026-09-14', '签章要求': '客户收货章'})
    assert result['recognition_status']['products'] == '未执行'
    assert result['recognition_status']['handwriting'] == '未执行'
    assert result['date_check']['status'] == '匹配'
    assert result['seal_check']['status'] == '匹配'
    assert result['overall'] == '通过'
    assert result['review_reasons'] == []


def test_provider_failure_does_not_silently_pass():
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            preview = None
            kw = {'_targets': {stage}, '_route': request.route, '_previous_fields': request.fields,
                  'seal_recognition_mode': request.seal_mode}
            if kw['seal_recognition_mode'] == 'qingtong_only':
                raise RuntimeError('timeout')
            return {'fields': kw['_previous_fields'], 'seal_check': {'recognized':'客户章', 'status':'匹配', 'reliable':True}}
    result = run_configured(Fake(), 'x', None, config={'seal':['paddle','qingtong']}, previous_fields={'签章要求':'客户章'}, ocr_backend='hybrid')
    assert result['overall'] == '需人工复核'
    assert result['recognition_variants']['seal'][1]['error'] == 'timeout'


def test_qingtong_only_never_calls_local_ocr_or_parsers(monkeypatch):
    import receipt_ocr.analyzer as module
    def forbidden(*a, **kw):
        raise AssertionError('unselected local stage was called')
    for name in ['recognize_text', 'decode_qr', 'parse_fields', 'parse_product_table', 'detect_seal_regions']:
        patch_dependency(monkeypatch, name, forbidden)
    patch_dependency(monkeypatch, 'resolve_backend', lambda b: 'paddle')
    analyzer = module.ReceiptAnalyzer()
    analyzer.seal_api = SimpleNamespace(enabled=True, recognize=lambda p: {'ok':True,'enabled':True,'response':{'data':{'img_0':[{'matched_seal':{'label':'测试有限公司收货专用章'},'text_formatted':'测试有限公司收货专用章'}]}}})
    result = run_configured(analyzer, 'unused.jpg', None, config={'seal':['qingtong']}, previous_fields={'签章要求':'测试有限公司收货专用章'}, ocr_backend='paddle')
    assert result['seal_check']['recognized']
    assert result['date_check']['status'] == '未执行'
    assert result['product_table']['status'] == '未执行'
    assert result['recognition_status']['fields'] == '未执行'
    assert result['overall'] == '需人工复核'


def test_provider_scope_is_restored():
    assert provider_allowed('paddle')
    with provider_scope({'paddle_v6'}):
        assert not provider_allowed('paddle')
        assert provider_allowed('paddle_v6')
    assert provider_allowed('paddle')


def test_task_keeps_plan_snapshot(tmp_path, monkeypatch):
    import json
    import app as web
    from receipt_ocr.database import Database
    database = Database(tmp_path / 'results.db')
    database.initialize()
    monkeypatch.setattr(web, 'database', database)
    monkeypatch.setattr(web, 'resolve_backend', lambda x: 'paddle')
    plan = {'fields':[], 'products':[], 'date':['paddle'], 'seal':[]}
    response = web.app.test_client().post('/api/tasks', json={'name':'date only', 'total':1, 'recognition_config':plan})
    assert response.status_code == 200
    saved = database.get_task(response.get_json()['id'])
    assert json.loads(saved['recognition_config']) == {**plan, 'handwriting': []}


def test_field_only_skips_products_date_and_seal(monkeypatch):
    import receipt_ocr.analyzer as module
    patch_dependency(monkeypatch, 'resolve_backend', lambda b:'paddle')
    patch_dependency(monkeypatch, 'recognize_text', lambda *a, **kw: [])
    patch_dependency(monkeypatch, 'decode_qr', lambda *a: '')
    patch_dependency(monkeypatch, '_recover_signature_requirement', lambda *a, **kw: None)
    def forbidden(*a, **kw):
        raise AssertionError('unselected stage executed')
    patch_dependency(monkeypatch, 'parse_product_table', forbidden)
    patch_dependency(monkeypatch, 'detect_seal_regions', forbidden)
    analyzer = module.ReceiptAnalyzer()
    monkeypatch.setattr(analyzer, '_recognize_receipt_date', forbidden)
    result = analyzer.analyze('unused.jpg', ocr_backend='paddle', _targets={'fields'})
    assert result['product_table']['status'] == '未执行'
    assert result['date_check']['status'] == '未执行'
    assert result['seal_check']['status'] == '未执行'


def test_direct_paddle_fallback_respects_selected_provider():
    from receipt_ocr.paddle_ocr import recognize_line, recognize_text
    with provider_scope({'paddle_v6'}):
        assert recognize_line('does-not-exist.png', model_variant='server') == []
        assert recognize_text('does-not-exist.png', model_variant='mobile') == []


def test_real_analyzer_reuses_page_between_fields_and_products(monkeypatch):
    from receipt_ocr import analyzer as module
    calls = []
    patch_dependency(monkeypatch, 'recognize_text', lambda *a, **kw: calls.append(kw['backend']) or [])
    patch_dependency(monkeypatch, 'decode_qr', lambda *a: '')
    patch_dependency(monkeypatch, '_recover_signature_requirement', lambda *a, **kw: None)
    patch_dependency(monkeypatch, 'resolve_backend', lambda b: 'paddle')
    analyzer = module.ReceiptAnalyzer()
    result = run_configured(analyzer, 'unused.jpg', None,
        config={'fields': ['paddle'], 'products': ['paddle']}, ocr_backend='paddle')
    assert calls == ['paddle']
    assert result['processing_timings']['page_ocr_cache_hits'] >= 1
    assert result['processing_timings']['steps']['stage.fields.paddle']['calls'] == 1
    assert result['processing_timings']['steps']['stage.products.paddle']['calls'] == 1
    run_configured(analyzer, 'unused.jpg', None,
        config={'fields': ['paddle'], 'products': ['paddle']}, ocr_backend='paddle')
    assert calls == ['paddle', 'paddle']


def configured_fake(*, date_status='匹配', reasons=()):
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            preview = None
            kw = {'_targets': {stage}, '_route': request.route, '_previous_fields': request.fields,
                  'seal_recognition_mode': request.seal_mode}
            assert preview is None
            stage = next(iter(kw['_targets']))
            return {
                'fields': {'要求到货': '2026-09-09', '签章要求': '客户章'},
                'field_metadata': {},
                'date_check': {'actual': '2026-09-09', 'status': date_status, 'reliable': True},
                'seal_check': {'recognized': '客户章', 'status': '匹配', 'reliable': True},
                'stage_review_reasons': list(reasons) if stage == 'fields' else [],
                'seal_regions': [{'x': .1, 'y': .2, 'width': .3, 'height': .4,
                                  'color': 'red', 'role': '收货客户章', 'pixel_ratio': .5}],
                'preview_date_box': [.5, .6, .2, .1],
            }
    return Fake()


@pytest.mark.parametrize('status,expected', [
    ('匹配', '通过'), ('不匹配', '需人工复核'), ('无法判断', '需人工复核'),
])
def test_configured_uses_shared_conservative_verdict(status, expected):
    result = run_configured(configured_fake(date_status=status), 'x', None,
        config={stage: ['paddle'] for stage in config_module.STAGES})
    assert result['overall'] == expected
    assert result['review_status'] == ('待复核' if expected == '需人工复核' else '无需复核')


def test_stage_review_blocker_survives_merge():
    result = run_configured(configured_fake(reasons=['关键字段低置信度：运单号']), 'x', None,
        config={stage: ['paddle'] for stage in config_module.STAGES})
    assert result['overall'] == '需人工复核'
    assert '关键字段低置信度：运单号' in result['review_reasons']


def test_preview_rendered_once_with_date_and_seal_evidence(monkeypatch):
    previews = []
    monkeypatch.setattr(config_module, 'annotate_image', lambda *args: previews.append(args))
    run_configured(configured_fake(), 'x', 'preview.jpg',
        config={stage: ['paddle'] for stage in config_module.STAGES})
    assert len(previews) == 1
    assert previews[0][2][0].role == '收货客户章'
    assert previews[0][3] == [.5, .6, .2, .1]


def test_remote_request_overlaps_local_work_and_is_used_once(monkeypatch):
    from threading import Event
    from receipt_ocr import analyzer as module
    from receipt_ocr.execution import measure
    remote_started, local_started = Event(), Event()
    requests = []
    def remote(source):
        with measure('test_remote_request'):
            assert provider_allowed('qingtong')
            assert not provider_allowed('paddle')
            requests.append(source)
            remote_started.set()
            assert local_started.wait(timeout=5)
            return {'enabled': True, 'ok': True, 'response': {'data': {'img_0': [{'matched_seal': {'label': '测试有限公司收货专用章'}, 'text_formatted': '测试有限公司收货专用章'}]}}}
    def local(*args, **kw):
        assert provider_allowed('paddle')
        assert not provider_allowed('qingtong')
        return []
    patch_dependency(monkeypatch, 'resolve_backend', lambda b: 'paddle')
    patch_dependency(monkeypatch, 'recognize_text', local)
    patch_dependency(monkeypatch, 'decode_qr', lambda *a: '')
    patch_dependency(monkeypatch, '_recover_signature_requirement', lambda *a, **kw: None)
    def parse_fields(rows):
        assert remote_started.wait(timeout=5)
        local_started.set()
        return {'签章要求': '测试有限公司收货专用章'}
    patch_dependency(monkeypatch, 'parse_fields', parse_fields)
    analyzer = module.ReceiptAnalyzer()
    analyzer.seal_api = SimpleNamespace(enabled=True, recognize=remote)
    result = run_configured(analyzer, 'unused.jpg', None,
        config={'fields': ['paddle'], 'seal': ['qingtong']}, ocr_backend='paddle')
    assert len(requests) == 1
    assert result['seal_check']['recognized'] == '测试有限公司收货专用章'
    assert result['processing_timings']['steps']['test_remote_request']['calls'] == 1
    assert provider_allowed('paddle') and provider_allowed('qingtong')


def test_prefetched_api_failure_keeps_local_fields_and_requires_review(monkeypatch):
    from receipt_ocr import analyzer as module
    patch_dependency(monkeypatch, 'resolve_backend', lambda b: 'paddle')
    patch_dependency(monkeypatch, 'recognize_text', lambda *a, **kw: [])
    patch_dependency(monkeypatch, 'decode_qr', lambda *a: '')
    patch_dependency(monkeypatch, '_recover_signature_requirement', lambda *a, **kw: None)
    patch_dependency(monkeypatch, 'parse_fields', lambda rows: {'签章要求': '客户章'})
    def remote(source):
        raise RuntimeError('remote timeout')
    analyzer = module.ReceiptAnalyzer()
    analyzer.seal_api = SimpleNamespace(enabled=True, recognize=remote)
    result = run_configured(analyzer, 'unused.jpg', None,
        config={'fields': ['paddle'], 'seal': ['qingtong']}, ocr_backend='paddle')
    assert result['fields']['签章要求'] == '客户章'
    assert result['overall'] == '需人工复核'
    assert result['recognition_status']['seal'] == '识别失败'
    assert any('remote timeout' in r for r in result['review_reasons'])


@pytest.mark.parametrize('remote_text,expected', [('测试有限公司收货专用章', '匹配'), ('其他单位专用章', '匹配')])
def test_qingtong_dual_verdict_is_preserved_with_local_variants(remote_text, expected):
    from receipt_ocr.qingtong_seal import compare_qingtong_seal
    external = {'enabled': True, 'ok': True, 'response': {'data': {'img_0': [
        {'matched_seal': {'label': '测试有限公司收货专用章'}, 'text_formatted': remote_text}]}}}
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            if request.seal_mode == 'qingtong_only':
                check = compare_qingtong_seal('测试有限公司收货专用章', external)
                return {'seal_check': check, 'stage_review_reasons': [] if check['reliable'] else ['印章内容无法可靠判断']}
            return {'seal_check': {'recognized': '别的章', 'status': '无法判断', 'reliable': False},
                    'stage_review_reasons': ['本地印章识别不可靠']}
    result = run_configured(Fake(), 'unused.jpg', None, config={'seal': ['paddle', 'qingtong']},
                            previous_fields={'签章要求': '测试有限公司收货专用章'}, ocr_backend='paddle')
    assert result['seal_check']['status'] == expected
    assert result['seal_check']['reliable'] == (expected == '匹配')
    assert '本地印章识别不可靠' not in result['review_reasons']
    assert not any('多种识别方式结果不一致' in reason for reason in result['review_reasons'])


def test_a_boxed_local_match_decides_over_a_qingtong_mismatch():
    """A local pass inside QingTong's box is a channel of its own.

    The API read characters that are printed inside the stamp itself; the local
    pass re-read the same box without them.  Under ``any`` the local reading
    decides, instead of being discarded as a local guess.
    """
    from receipt_ocr.qingtong_seal import compare_qingtong_seal
    required = '测试有限公司收货专用章'
    external = {'enabled': True, 'ok': True, 'response': {'data': {'img_0': [
        {'matched_seal': {'label': f'{required}年月'}, 'text_formatted': f'{required}年月'}]}}}
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            if stage != 'seal':
                return {'fields': {}, 'stage_review_reasons': []}
            if request.seal_mode == 'qingtong_only':
                check = compare_qingtong_seal(required, external)
                return {'seal_check': check, 'stage_review_reasons': []}
            return {
                'seal_check': {
                    'requirement': required, 'recognized': required, 'status': '匹配',
                    'reliable': True, 'region_source': '清瞳印章区域',
                },
                'stage_review_reasons': [],
            }
    result = run_configured(Fake(), 'unused.jpg', None, config={'seal': ['paddle', 'qingtong']},
                            previous_fields={'签章要求': required}, ocr_backend='paddle')
    assert result['seal_check']['status'] == '匹配'
    assert result['seal_check']['reliable'] is True
    assert result['seal_check']['source'] == '本地识别（清瞳印章区域）'
    assert result['seal_check']['local_channel']['recognized'] == required
    assert not any('印章' in reason for reason in result['review_reasons'])


def test_an_unmarked_local_variant_still_does_not_decide():
    """A local pass that detected its own region stays evidence-only."""
    from receipt_ocr.qingtong_seal import compare_qingtong_seal
    required = '测试有限公司收货专用章'
    external = {'enabled': True, 'ok': True, 'response': {'data': {'img_0': [
        {'matched_seal': {'label': f'{required}年月'}, 'text_formatted': f'{required}年月'}]}}}
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            if stage != 'seal':
                return {'fields': {}, 'stage_review_reasons': []}
            if request.seal_mode == 'qingtong_only':
                check = compare_qingtong_seal(required, external)
                return {'seal_check': check, 'stage_review_reasons': []}
            return {'seal_check': {'requirement': required, 'recognized': required,
                                   'status': '匹配', 'reliable': True},
                    'stage_review_reasons': []}
    result = run_configured(Fake(), 'unused.jpg', None, config={'seal': ['paddle', 'qingtong']},
                            previous_fields={'签章要求': required}, ocr_backend='paddle')
    assert result['seal_check']['status'] == '不匹配'
    assert 'local_channel' not in result['seal_check']


@pytest.mark.parametrize('winning_channel', ['template', 'ocr', 'danzhengtong'])
def test_any_one_of_three_exact_seal_channels_passes_full_pipeline(monkeypatch, winning_channel):
    from receipt_ocr import danzhengtong
    from receipt_ocr.parsing_seals import compare_seal_text_strict
    from receipt_ocr.qingtong_seal import compare_qingtong_seal
    required, wrong = '测试有限公司收货专用章', '错误单位签收章'
    remote = compare_seal_text_strict(required, [required if winning_channel == 'danzhengtong' else wrong])
    remote.update(backend='单证通', source='单证通', recognition_mode='danzhengtong', simulated=False)
    monkeypatch.setattr(danzhengtong, 'stage_result', lambda *a, **kw: {'seal_check': remote})
    external = {'ok': True, 'response': {'data': {'img_0': [{
        'matched_seal': {'label': required if winning_channel == 'template' else wrong},
        'text_formatted': required if winning_channel == 'ocr' else wrong}]}}}
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, context, stage, request):
            if stage == 'fields':
                return {'fields': {'签章要求': required, '要求到货': '2025-02-10'}}
            if stage == 'date':
                return {'date_check': {'required': '2025-02-10', 'actual': '2025-02-10', 'status': '匹配', 'reliable': True}}
            if stage == 'products':
                return {'product_table': {'rows': [], 'status': '已执行'}}
            check = compare_qingtong_seal(required, external)
            return {'seal_check': check, 'stage_review_reasons': [] if check['reliable'] else ['印章内容无法可靠判断']}
    result = run_configured(Fake(), 'unused.jpg', None,
        config={'fields': ['paddle'], 'products': ['paddle'], 'date': ['paddle'], 'seal': ['qingtong', 'danzhengtong']})
    assert result['seal_check']['recognized'] == required
    assert result['seal_check']['status'] == '匹配'
    assert result['seal_check']['reliable'] is True
    assert result['overall'] == '通过'
    assert not result['review_reasons']


def test_danzhengtong_match_is_not_vetoed_by_other_provider_failure(monkeypatch):
    from receipt_ocr import danzhengtong
    from receipt_ocr.parsing_seals import compare_seal_text_strict
    required = '测试有限公司收货专用章'
    check = compare_seal_text_strict(required, [required])
    check.update(backend='单证通', source='单证通', recognition_mode='danzhengtong', simulated=False)
    monkeypatch.setattr(danzhengtong, 'stage_result', lambda *a, **kw: {'seal_check': check})
    class Fake:
        seal_api = SimpleNamespace(enabled=True)
        def run_stage(self, *a, **kw):
            raise RuntimeError('qingtong timeout')
    result = run_configured(Fake(), 'unused.jpg', None, config={'seal': ['qingtong', 'danzhengtong']},
                            previous_fields={'签章要求': required})
    assert result['seal_check']['status'] == '匹配'
    assert result['seal_check']['recognized'] == required
    assert result['seal_check']['reliable'] is True
    assert not any('timeout' in reason for reason in result['review_reasons'])
    assert result['recognition_variants']['seal'][0]['error'] == 'qingtong timeout'
