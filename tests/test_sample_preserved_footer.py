"""Preserved provider results retain confirmed page-layout evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from receipt_ocr.application.deferred_document import combine_saved
from receipt_ocr.application.retained_providers import retained_branch, merge_retained_result
from receipt_ocr.application.saved_footer import saved_footer_evidence
from receipt_ocr.domain.issues import issue
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from receipt_ocr.samples.preservation import repair_preserved_sample_footers
from sample_support import SampleCenter
from test_sample_center import samples
from test_sample_provider_preservation import legacy_result
from test_danzhengtong_jobs import COMMIT


FOOTER_WARNING = '签收页脚尚未确认，请核对本页或关联续页'
DOCUMENT_WARNING = '文档版式仍需人工核对'


def receipt_result(present=True):
    result = legacy_result()
    result['document_type'] = {
        'type': 'receipt', 'label': '回单', 'reliable': True,
        'confidence': 1, 'reasons': [],
    }
    result['signature_footer'] = ({'present': present, 'method': 'printed_requirement'}
                                  if present is not None else {})
    result['ocr_stage_backends'] = {'page': {'id': 'paddle_v6'}}
    return result


def remote_plan():
    plan = {stage: ['danzhengtong'] for stage in ('fields', 'handwriting', 'date', 'seal')}
    plan['products'] = []
    return plan


def saved_remote():
    return {'mode': 'real', 'simulated': False, 'status': 'completed',
            'reqUuid': 'already-saved-request', 'result': {'commitResult': deepcopy(COMMIT)}}


def footer_issues(result):
    return [issue for issue in result['review_issues'] if issue['code'] == 'footer_unconfirmed']


def saved_rows(text='签章要求：客户收货专用章'):
    return [{'text': text, 'confidence': .98, 'x': .05, 'y': .471, 'width': .45, 'height': .02}]


@pytest.mark.parametrize('legacy_footer', ['absent', None, {}])
def test_legacy_history_recovers_footer_from_saved_full_page_rows(legacy_footer):
    original = receipt_result()
    if legacy_footer == 'absent':
        del original['signature_footer']
    else:
        original['signature_footer'] = legacy_footer
    original['ocr_observations'] = saved_rows()
    before = deepcopy(original)
    branch = retained_branch([(original, {})], remote_plan(), 'same-source')
    context = branch['snapshot']['context']
    assert context['has_footer'] is True
    assert context['footer_detection']['method'] == 'printed_requirement'
    assert context['footer_detection']['anchor'] == original['ocr_observations'][0]
    assert branch['base']['signature_footer'] == context['footer_detection']
    assert branch['snapshot']['evidence']['signature_footer'] == context['footer_detection']
    assert original == before


def test_old_snapshot_recovers_footer_from_saved_rows_without_new_recognition(tmp_path):
    branch = retained_branch([(receipt_result(), {})], remote_plan(), 'same-source')
    snapshot = branch['snapshot']
    snapshot['context'].update(has_footer=False, footer_detection={})
    snapshot['evidence'].pop('signature_footer', None)
    snapshot['evidence']['ocr_observations'] = saved_rows()
    before = deepcopy(snapshot)
    combined = combine_saved(tmp_path / 'not-read.png', 'receipt.png', snapshot,
                             remote=saved_remote(), preserve_provider_checks=True)
    assert not footer_issues(combined)
    assert combined['signature_footer']['present'] is True
    assert combined['signature_footer']['anchor'] == saved_rows()[0]
    assert snapshot == before


def test_saved_footer_requires_spatial_ocr_evidence_and_respects_explicit_absence():
    sparse = {'ocr_observations': saved_rows('2025年10月27日') + saved_rows('盖章'),
              'date_check': {'status': '匹配', 'reliable': True},
              'seal_check': {'status': '匹配', 'reliable': True},
              'fields': {'签章要求': '客户收货专用章'}}
    assert saved_footer_evidence(sparse)['present'] is False
    assert saved_footer_evidence({'date_check': sparse['date_check'],
                                 'seal_check': sparse['seal_check']}) == {}
    explicit = {'signature_footer': {'present': False, 'method': 'insufficient_footer_evidence'},
                'ocr_observations': saved_rows()}
    assert saved_footer_evidence(explicit) == explicit['signature_footer']


@pytest.mark.parametrize('legacy_footer', [None, {}])
def test_retained_merge_does_not_overwrite_recovered_footer_with_empty_legacy_metadata(legacy_footer):
    footer = saved_footer_evidence({'ocr_observations': saved_rows()})
    branch = {'base': {'signature_footer': legacy_footer}, 'snapshot': {}}
    combined = {'signature_footer': footer}
    assert merge_retained_result(branch, combined)['signature_footer'] == footer


def test_legacy_provider_history_restores_confirmed_footer_without_assembly_snapshot():
    original = receipt_result()
    before = deepcopy(original)
    branch = retained_branch([(original, {})], remote_plan(), 'same-source')
    assert branch['snapshot']['context']['has_footer'] is True
    assert branch['snapshot']['context']['footer_detection'] == original['signature_footer']
    assert original == before


@pytest.mark.parametrize('present', [True, False, None])
def test_saved_incremental_combine_uses_explicit_footer_evidence(tmp_path, present):
    original = receipt_result(present)
    branch = retained_branch([(original, {})], remote_plan(), 'same-source')
    snapshot = branch['snapshot']
    # Explicit page evidence wins over an inconsistent saved boolean in either
    # direction; absent evidence must never invent a confirmed footer.
    snapshot['context']['has_footer'] = present is False
    before = deepcopy(snapshot)
    combined = combine_saved(tmp_path / 'receipt.png', 'receipt.png', snapshot,
                             remote=saved_remote(), preserve_provider_checks=True)
    assert bool(footer_issues(combined)) is (present is not True)
    assert (FOOTER_WARNING in combined['review_reasons']) is (present is not True)
    assert combined['signature_footer'] == original['signature_footer']
    # Correcting layout evidence must not erase an independent date mismatch.
    local_date = next(item for item in combined['recognition_variants']['date']
                      if item['method'] == 'paddle_v6')
    assert local_date['details']['date_check']['status'] == '不匹配'
    assert snapshot == before


def applied_preserved_return(samples):
    ctx, client = samples
    service = SampleCenter(ctx)
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    service.store.enqueue(['one.png'], {
        'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
        'recognition_config': settings['recognition_config'],
    }, settings)
    job = service.store.claim()
    service.store.finish(job, receipt_result(), 'old.jpg')
    response = client.post('/api/samples/run', json={
        'filenames': ['one.png'], 'mode': 'danzhengtong',
    })
    assert response.status_code == 202
    service = SampleCenter(ctx)
    item = next(row for row in service.listing()['items'] if row['filename'] == 'one.png')
    current_job = service.store.get(item['job_id'])
    branch = json.loads(current_job['branch_snapshot_json'])
    branch['snapshot']['context']['has_footer'] = False
    digest = hashlib.sha256(service.library.source('one.png').read_bytes()).hexdigest()
    with service.database.connect() as con:
        con.execute('UPDATE recognition_jobs SET branch_snapshot_json=? WHERE id=?',
                    (json.dumps(branch, ensure_ascii=False), current_job['id']))
        # This fixture is a historical single-scene return, not an unfinished
        # modern page + independent-stamp run. Its empty continuation marker
        # permits reuse of the saved page seal text without any new requests.
        con.execute("""UPDATE danzhengtong_jobs SET status='received',seal_requests_json='{}',
            req_uuid='already-saved-request',result_json=?,source_hash=?,next_query_at=''
            WHERE job_id=?""", (json.dumps(saved_remote()['result']), digest, current_job['id']))

    def reject_network(task):
        raise AssertionError('a saved return must not upload, submit, or query again')

    service.worker.remote.client_factory = reject_network
    assert service.worker.remote.step()
    result = service.database.get_result(item['result_id'])
    return service, item, result


def test_worker_applies_saved_remote_return_using_repaired_footer_context(samples):
    service, item, result = applied_preserved_return(samples)
    assert not footer_issues(result)
    assert FOOTER_WARNING not in result['review_reasons']
    assert result['signature_footer']['present'] is True
    assert service.store.remote_detail(item['job_id'])['status'] == 'applied'


def damaged_applied_footer(samples):
    service, item, _ = applied_preserved_return(samples)
    with service.database.connect() as con:
        row = con.execute('SELECT result_json FROM results WHERE id=?', (item['result_id'],)).fetchone()
        result = json.loads(row['result_json'])
        result['review_issues'].extend([
            issue('footer_unconfirmed', 'document', FOOTER_WARNING),
            issue('review_required', 'document', DOCUMENT_WARNING),
        ])
        result['review_reasons'].extend([FOOTER_WARNING, DOCUMENT_WARNING])
        result.update(overall='需人工复核', final_result='需人工复核', review_status='待复核')
        payload = json.dumps(result, ensure_ascii=False)
        con.execute("""UPDATE results SET result_json=?,original_result_json=?,
            overall='需人工复核',final_result='需人工复核',review_status='待复核' WHERE id=?""",
            (payload, payload, item['result_id']))
        job = con.execute('SELECT branch_snapshot_json FROM recognition_jobs WHERE id=?',
                          (item['job_id'],)).fetchone()
        branch = json.loads(job['branch_snapshot_json'])
        branch['snapshot']['context']['has_footer'] = False
        con.execute('UPDATE recognition_jobs SET branch_snapshot_json=? WHERE id=?',
                    (json.dumps(branch, ensure_ascii=False), item['job_id']))
    return service, item, result


def test_historical_footer_repair_is_local_audited_and_idempotent(samples):
    service, item, before = damaged_applied_footer(samples)
    remote_before = service.store.remote_detail(item['job_id'])
    history_before = service.database.history(item['result_id'])
    assert repair_preserved_sample_footers(service.store) == 1
    repaired = service.database.get_result(item['result_id'])
    assert not footer_issues(repaired)
    assert FOOTER_WARNING not in repaired['review_reasons']
    assert DOCUMENT_WARNING in repaired['review_reasons']
    assert issue('review_required', 'document', DOCUMENT_WARNING) in repaired['review_issues']
    assert repaired['overall'] == '需人工复核'
    assert repaired['fields'] == before['fields']
    assert repaired['recognition_variants'] == before['recognition_variants']
    for key in ('date_check', 'seal_check', 'signature_check'):
        assert repaired[key] == before[key]
    with service.database.connect() as con:
        original = json.loads(con.execute('SELECT original_result_json FROM results WHERE id=?',
                                         (item['result_id'],)).fetchone()[0])
    assert not footer_issues(original)
    assert DOCUMENT_WARNING in original['review_reasons']
    branch = json.loads(service.store.get(item['job_id'])['branch_snapshot_json'])
    assert branch['snapshot']['context']['has_footer'] is True
    assert service.store.remote_detail(item['job_id']) == remote_before
    history = service.database.history(item['result_id'])
    assert len(history) == len(history_before) + 1
    assert history[0]['action'] == '补充识别页脚状态修复'
    assert footer_issues(json.loads(history[0]['before_json']))
    assert not footer_issues(json.loads(history[0]['after_json']))
    assert repair_preserved_sample_footers(service.store) == 0
    assert service.database.get_result(item['result_id']) == repaired
    assert service.database.history(item['result_id']) == history


@pytest.mark.parametrize('saved_evidence', ['footer_rows', 'no_footer_rows', 'explicit_absence'])
def test_historical_repair_replays_legacy_ocr_only_when_footer_is_supported(samples, saved_evidence):
    service, item, damaged = damaged_applied_footer(samples)
    observations = saved_rows() if saved_evidence != 'no_footer_rows' else saved_rows('日期：2025-10-27')
    damaged['signature_footer'] = ({'present': False, 'method': 'insufficient_footer_evidence'}
                                   if saved_evidence == 'explicit_absence' else None)
    damaged['ocr_observations'] = observations
    branch = json.loads(service.store.get(item['job_id'])['branch_snapshot_json'])
    branch['snapshot']['context'].update(has_footer=False, footer_detection={})
    branch['snapshot']['evidence'].pop('signature_footer', None)
    branch['snapshot']['evidence']['ocr_observations'] = observations
    branch['base']['signature_footer'] = None
    branch['base']['ocr_observations'] = observations
    with service.database.connect() as con:
        payload = json.dumps(damaged, ensure_ascii=False)
        con.execute('UPDATE results SET result_json=?,original_result_json=? WHERE id=?',
                    (payload, payload, item['result_id']))
        con.execute('UPDATE recognition_jobs SET branch_snapshot_json=? WHERE id=?',
                    (json.dumps(branch, ensure_ascii=False), item['job_id']))
    before = service.database.get_result(item['result_id'])
    history_before = service.database.history(item['result_id'])
    remote_before = service.store.remote_detail(item['job_id'])
    expected = saved_evidence == 'footer_rows'
    assert repair_preserved_sample_footers(service.store) == int(expected)
    result = service.database.get_result(item['result_id'])
    assert service.store.remote_detail(item['job_id']) == remote_before
    if not expected:
        assert result == before
        assert service.database.history(item['result_id']) == history_before
        assert service.store.get(item['job_id'])['branch_snapshot_json'] == json.dumps(branch, ensure_ascii=False)
        return
    assert not footer_issues(result)
    assert result['signature_footer']['present'] is True
    assert result['signature_footer']['method'] == 'printed_requirement'
    assert result['signature_footer']['anchor'] == observations[0]
    assert result['fields'] == before['fields']
    assert result['recognition_variants'] == before['recognition_variants']
    assert DOCUMENT_WARNING in result['review_reasons']
    assert issue('review_required', 'document', DOCUMENT_WARNING) in result['review_issues']
    repaired_branch = json.loads(service.store.get(item['job_id'])['branch_snapshot_json'])
    assert repaired_branch['snapshot']['context']['has_footer'] is True
    assert repaired_branch['base']['signature_footer'] == result['signature_footer']
    with service.database.connect() as con:
        original = json.loads(con.execute('SELECT original_result_json FROM results WHERE id=?',
                                         (item['result_id'],)).fetchone()[0])
    assert original['signature_footer'] == result['signature_footer']
    assert not footer_issues(original)
    assert repair_preserved_sample_footers(service.store) == 0


@pytest.mark.parametrize('guard', [
    'human_note', 'human_override', 'human_decision', 'human_signature', 'human_history',
    'waiting', 'received', 'not_latest',
])
def test_historical_footer_repair_preserves_manual_pending_and_superseded_results(samples, guard):
    service, item, damaged = damaged_applied_footer(samples)
    with service.database.connect() as con:
        if guard == 'human_note':
            con.execute("UPDATE results SET human_note='人工备注' WHERE id=?", (item['result_id'],))
        elif guard == 'human_decision':
            con.execute("UPDATE results SET review_status='确认通过',final_result='通过' WHERE id=?",
                        (item['result_id'],))
        elif guard in {'human_override', 'human_signature'}:
            if guard == 'human_override':
                damaged['page_review_override'] = {'fields': {'仓库接收人': '人工值'}}
            else:
                damaged['signature_check']['source'] = '人工复核'
            con.execute('UPDATE results SET result_json=? WHERE id=?',
                        (json.dumps(damaged, ensure_ascii=False), item['result_id']))
        elif guard in {'waiting', 'received'}:
            con.execute('UPDATE danzhengtong_jobs SET status=? WHERE job_id=?', (guard, item['job_id']))
    if guard == 'human_history':
        service.database.review_result(item['result_id'], result=damaged,
            review_status='待复核', final_result='需人工复核', note='', action='人工修改')
    elif guard == 'not_latest':
        settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
        service.store.enqueue(['one.png'], {
            'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
            'recognition_config': settings['recognition_config'],
        }, settings)
    before = service.database.get_result(item['result_id'])
    job_before = service.store.get(item['job_id'])
    history_before = service.database.history(item['result_id'])
    remote_before = service.store.remote_detail(item['job_id'])
    assert repair_preserved_sample_footers(service.store) == 0
    assert service.database.get_result(item['result_id']) == before
    assert footer_issues(before)
    assert service.store.get(item['job_id']) == job_before
    assert service.database.history(item['result_id']) == history_before
    assert service.store.remote_detail(item['job_id']) == remote_before
