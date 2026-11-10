"""Date display selection uses saved observations, never the requested date as a replacement."""
from copy import deepcopy
from io import BytesIO

from openpyxl import load_workbook
import pytest

from receipt_ocr.application.assembly import assemble_result, merge_primary, select_variants
from receipt_ocr.domain.date_selection import refresh_date_selection, select_date_variant
from receipt_ocr.domain.fields.schema import project_fields
from receipt_ocr.domain.parsing.parsing_dates import compare_dates, parse_date
from receipt_ocr.domain.stages import STAGES
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from sample_support import SampleCenter
from test_printed_field_pipeline import context
from test_sample_center import samples


def variant(method, actual, *, required='2025-02-22', reliable=True):
    check = {**compare_dates(required, parse_date(actual)), 'reliable': reliable,
             'source': method, 'raw_text': actual}
    return {'method': method, 'result': {'date_check': check, 'date_ocr_texts': [actual],
            'processing_artifacts': {'date': [{'original_url': f'/files/artifacts/{method}.png'}]}}}


@pytest.mark.parametrize('dates,reliability,selected', [
    (['2025-02-02', '2025-02-21'], [False, True], 1),
    (['2025-02-02', '2025-02-21'], [True, False], 1),
    (['2025-02-21', '2025-02-23'], [False, True], 0),
    (['2025-02-23', '2025-02-21'], [True, False], 0),
])
def test_mismatches_choose_nearest_date_with_stable_ties(dates, reliability, selected):
    candidates = [variant(str(index), value, reliable=reliable)
                  for index, (value, reliable) in enumerate(zip(dates, reliability))]
    before = deepcopy(candidates)
    assert select_date_variant(candidates) is candidates[selected]
    assert candidates == before


@pytest.mark.parametrize('required,dates,selected', [
    ('2025-03-01', ['2025-02-28', '2025-03-03'], 0),
    ('2024-03-01', ['2024-02-28', '2024-03-02'], 1),
    ('2025-01-01', ['2024-12-31', '2025-01-03'], 0),
    ('2025-02-22', ['2075-02-22', '2025-02-21'], 1),
    ('２０２５／２／２２', ['2025-02-02', '2025-02-21'], 1),
])
def test_distance_uses_actual_calendar_days(required, dates, selected):
    candidates = [variant(str(index), value, required=required) for index, value in enumerate(dates)]
    assert select_date_variant(candidates, required_date=required) is candidates[selected]


def test_reliable_match_precedes_unreliable_equal_date_and_invalid_or_inactive_candidates():
    candidates = [variant('uncertain', '2025-02-22', reliable=False),
                  variant('matching', '2025-02-22')]
    assert select_date_variant(candidates) is candidates[1]
    broken = variant('invalid', '2025-02-21')
    broken['result']['date_check']['actual'] = '2025-02-30'
    partial = variant('partial', '2025-02-21')
    partial['result']['date_check'].update(actual='', actual_display='2025-02-__')
    failed = {**variant('failed', '2025-02-22'), 'error': 'read failed'}
    skipped = variant('skipped', '2025-02-22')
    skipped['result']['date_check']['execution_status'] = 'skipped'
    available = variant('available', '2025-02-21')
    assert select_date_variant([broken, partial, failed, skipped, available]) is available
    assert partial['result']['date_check']['actual_display'] == '2025-02-__'


def test_missing_requirement_keeps_original_order_without_completing_partial_dates():
    candidates = [variant('first', '2025-02-02'), variant('second', '2025-02-21')]
    assert select_date_variant(candidates, required_date='unknown') is candidates[0]
    for candidate in candidates:
        candidate['result']['date_check'].update(actual='', actual_display='2025-02-__')
    assert select_date_variant(candidates) is candidates[0]
    assert candidates[0]['result']['date_check']['actual'] == ''


@pytest.mark.parametrize('mode,status,reliable', [
    ('any', '不匹配', True), ('all', '需人工复核', False), ('none', '未核对', True),
])
def test_assembly_selects_existing_nearest_check_without_changing_acceptance(mode, status, reliable):
    candidates = [variant('local', '2025-02-02', reliable=False), variant('danzhengtong', '2025-02-21')]
    before = deepcopy(candidates)
    acceptance = {'date_match_mode': mode}
    combined, decisions, primary = select_variants('date', candidates, acceptance, required_date='2025-02-22')
    assert primary is candidates[1]['result'] and decisions[0] is candidates[1]
    output = {'fields': {'要求到货': '2025-02-22'}, 'processing_artifacts': {'date': [], 'seals': []}}
    merge_primary(output, 'date', primary, candidates, candidates, combined, acceptance)
    output = project_fields(output)
    assert output['date_check']['actual'] == output['fields']['签收日期'] == '2025-02-21'
    assert output['date_check']['source'] == 'danzhengtong'
    assert output['date_check']['status'] == status and output['date_check']['reliable'] is reliable
    assert len(output['processing_artifacts']['date']) == 2
    assert candidates == before


@pytest.mark.parametrize('mode,status', [('any', '不匹配'), ('all', '需人工复核'), ('none', '未核对')])
def test_complete_assembly_keeps_every_providers_date_text_and_selected_backend(tmp_path, mode, status):
    results = {stage: [] for stage in STAGES}
    results['date'] = [variant('paddle_v6', '2025-02-02', reliable=False),
                       variant('danzhengtong', '2025-02-21')]
    before = deepcopy(results)
    plan = {stage: [] for stage in STAGES}
    plan.update(date=['paddle_v6', 'danzhengtong'], acceptance={'date_match_mode': mode})
    output = assemble_result(context(tmp_path), plan, results, [], {}, {'要求到货': '2025-02-22'}, None, None, {})
    assert output['fields']['签收日期'] == output['date_check']['actual'] == '2025-02-21'
    assert output['date_check']['status'] == status
    if mode == 'none':
        assert output['date_check']['reliable'] is True
    assert output['ocr_stage_backends']['date']['id'] == 'danzhengtong'
    assert [value['details']['date_ocr_texts'] for value in output['recognition_variants']['date']] == [
        ['2025-02-02'], ['2025-02-21']]
    assert len(output['processing_artifacts']['date']) == 2
    assert results == before


def saved_result(mode='any'):
    variants = [variant('local', '2025-02-02', reliable=False), variant('danzhengtong', '2025-02-21')]
    return {'fields': {'要求到货': '2025-02-22', '签收日期': '2025-02-02'},
            'date_check': deepcopy(variants[0]['result']['date_check']),
            'recognition_variants': {'date': [{'method': value['method'], 'details': value['result'],
                                              'value': [value['result']['date_check']['actual'], '不匹配']}
                                             for value in variants]},
            'processing_artifacts': {'date': [{'original_url': '/files/artifacts/local.png'}]},
            'date_ocr_texts': ['2025年2月2日'],
            'recognition_config': {'acceptance': {'date_match_mode': mode}},
            'review_status': '待复核', 'overall': '需人工复核',
            'review_reasons': ['保留原有问题'], 'seal_check': {'status': '匹配', 'reliable': True}}


@pytest.mark.parametrize('mode,status,reliable', [
    ('any', '不匹配', True), ('all', '需人工复核', False), ('none', '未核对', True),
])
def test_saved_date_refresh_changes_only_the_aggregate_and_machine_date(mode, status, reliable):
    current = saved_result(mode)
    before = deepcopy(current)
    updated = refresh_date_selection(current)
    assert updated['fields']['签收日期'] == updated['date_check']['actual'] == '2025-02-21'
    assert updated['date_check']['source'] == 'danzhengtong'
    assert updated['date_check']['status'] == status and updated['date_check']['reliable'] is reliable
    if mode == 'any':
        assert updated['date_check'] == current['recognition_variants']['date'][1]['details']['date_check']
        assert updated['date_check']['message'] == '实际收货比要求日期早 1 天'
    assert {key: value for key, value in updated.items() if key not in {'fields', 'date_check'}} == {
        key: value for key, value in current.items() if key not in {'fields', 'date_check'}}
    assert current == before
    assert refresh_date_selection(updated) == updated


@pytest.mark.parametrize('change', [
    {'date_check': {'source': '人工复核', 'human_confirmed': True}},
    {'field_metadata': {'签收日期': {'source': '人工复核'}}},
    {'review_status': '确认通过'}, {'review_status': '确认不通过'},
    {'page_review_override': {'date_check': {'actual': '2025-02-02'}}},
])
def test_saved_date_refresh_never_overwrites_human_dates(change):
    current = saved_result()
    current.update(change)
    assert refresh_date_selection(current) == current


@pytest.mark.parametrize('pending', [True, False])
def test_saved_date_refresh_waits_for_pending_provider_and_ignores_failed_candidates(pending):
    current = saved_result()
    remote = current['recognition_variants']['date'][1]
    remote['pending' if pending else 'error'] = True if pending else 'read failed'
    assert refresh_date_selection(current) == current


def test_saved_date_refresh_waits_for_an_active_remote_trace():
    current = saved_result()
    current['danzhengtong'] = {'mode': 'real', 'status': 'waiting'}
    assert refresh_date_selection(current) == current


def test_persisted_date_selection_is_shared_by_detail_list_filters_statistics_and_export(samples):
    ctx, client = samples
    service = SampleCenter(ctx)
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    service.store.enqueue(['one.png'], {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
                                      'recognition_config': settings['recognition_config']}, settings)
    job = service.store.claim()
    service.store.finish(job, saved_result())
    result_id = service.store.get(job['id'])['result_id']
    current = service.database.get_result(result_id)
    updated = refresh_date_selection(current)
    service.database.review_result(result_id, result=updated, review_status=updated['review_status'],
                                   final_result=updated['final_result'], note='', action='日期候选选择规则更新')
    detail = client.get(f'/api/samples/results/{result_id}').get_json()
    assert detail['date_check']['actual'] == detail['fields']['签收日期'] == '2025-02-21'
    listing = client.get('/api/samples').get_json()
    assert next(item for item in listing['items'] if item['filename'] == 'one.png')['result_filters']['date']['value'] == '2025-02-21'
    statistics = client.get('/api/samples/statistics').get_json()
    assert next(item for item in statistics['items'] if item['filename'] == 'one.png')['date']['value'] == '2025-02-21'
    export = client.get('/api/samples/statistics/export?tab=date')
    assert export.status_code == 200
    worksheet = load_workbook(BytesIO(export.data))['样单明细']
    rows = list(worksheet.values)
    header_index = next(index for index, row in enumerate(rows) if row[0] == '样单编号')
    date_column = rows[header_index].index('识别日期 · 识别内容')
    row = next(row for row in rows[header_index + 1:] if row[1] == 'one.png')
    assert row[date_column].date().isoformat() == '2025-02-21'
    assert service.database.get_result(result_id)['recognition_variants'] == current['recognition_variants']
