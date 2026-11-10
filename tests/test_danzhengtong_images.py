"""Background image failures never delay or overwrite recognized text."""
from dataclasses import replace
import json

from PIL import Image
import pytest

from receipt_ocr import danzhengtong as dzt
from receipt_ocr import danzhengtong_seal as seals
from receipt_ocr.jobs.danzhengtong_worker import DanzhengtongWorker
from test_danzhengtong_jobs import runtime
from test_danzhengtong_seal_jobs import stamps, start_seal_only, complete


@pytest.fixture
def images(stamps, monkeypatch):
    monkeypatch.setattr(dzt, '_software_settings', dzt.Settings(download_seal_images=True))
    client = stamps.worker._seal_client({})
    original = client.get_result

    def get_result(uuid, **kwargs):
        result = original(uuid, **kwargs)
        result['data']['imgUrls'] = ['https://images.example/' + uuid + '.png?signature=private']
        return result

    monkeypatch.setattr(client, 'get_result', get_result)
    stamps.downloads = []

    def download(url, destination):
        # The first download can only begin after the text transaction commits.
        result = stamps.store.database.get_result(stamps.store.get(stamps.image_job)['result_id'])
        assert result['seal_check']['recognized'] == '测试客户专用章'
        assert stamps.store.remote_detail(stamps.image_job)['status'] == 'applied'
        stamps.downloads.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (17, 11), 'red').save(destination)
        return destination

    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.download_returned_image', download)
    stamps.image_job = start_seal_only(stamps)
    return stamps


def result(service):
    return service.store.database.get_result(service.store.get(service.image_job)['result_id'])


def test_text_commits_before_download_and_download_queue_survives_restart(images):
    complete(images, images.image_job)
    before = result(images)
    assert images.downloads == []
    assert all(item['returned_image_status'] == 'pending' for item in before['processing_artifacts']['seals'])
    submissions = len(images.stamp_state['submissions'])
    restarted = DanzhengtongWorker(images.store, images.service)
    assert restarted.images.step()
    assert restarted.images.step()
    assert not restarted.images.step()
    after = result(images)
    assert len(images.downloads) == 2
    assert len(images.stamp_state['submissions']) == submissions
    assert after['seal_check'] == before['seal_check']
    assert all(item['returned_image_status'] == 'downloaded' for item in after['processing_artifacts']['seals'])
    calls = images.store.database.latest_recognition_calls(after['id'])['calls']
    assert all(call['transport']['apply_started_at'] <= call['transport']['applied_at']
               <= call['transport']['image_download']['started_at'] for call in calls)


def test_disabled_before_recognition_never_queues_or_downloads(images, monkeypatch):
    monkeypatch.setattr(dzt, '_software_settings', replace(dzt.Settings.load(), download_seal_images=False))
    complete(images, images.image_job)
    assert not images.worker.images.step()
    assert images.downloads == []
    assert result(images)['seal_check']['recognized'] == '测试客户专用章'
    assert all(item['returned_image_status'] == 'disabled' for item in result(images)['processing_artifacts']['seals'])


def test_turning_off_also_skips_already_queued_images(images, monkeypatch):
    complete(images, images.image_job)
    monkeypatch.setattr(dzt, '_software_settings', replace(dzt.Settings.load(), download_seal_images=False))
    assert images.worker.images.step()
    assert images.worker.images.step()
    assert images.downloads == []
    assert all(item['returned_image_status'] == 'disabled' for item in result(images)['processing_artifacts']['seals'])


def test_download_failure_keeps_saved_result_and_hides_signed_url(images, monkeypatch):
    complete(images, images.image_job)
    before = result(images)

    def fail(*_):
        raise TimeoutError('https://images.example/file.png?signature=secret&X-Amz-Credential=credential')

    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.download_returned_image', fail)
    assert images.worker.images.step()
    after = result(images)
    assert after['seal_check'] == before['seal_check']
    artifact = after['processing_artifacts']['seals'][0]
    assert artifact['returned_image_status'] == 'unavailable'
    assert artifact['returned_image_url'] == ''
    assert 'secret' not in artifact['returned_image_error']
    assert 'credential' not in artifact['returned_image_error']
    assert images.store.remote_detail(images.image_job)['status'] == 'applied'


def test_result_edits_during_download_are_preserved(images, monkeypatch):
    complete(images, images.image_job)
    download = seals.download_returned_image

    def edit_and_download(url, destination):
        saved = result(images)
        saved['fields']['客户名称'] = '人工修正客户'
        images.store.database.review_result(saved['id'], result=saved, review_status='确认通过',
            final_result='已通过', note='人工备注', action='测试复核')
        return download(url, destination)

    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.download_returned_image', edit_and_download)
    assert images.worker.images.step()
    saved = result(images)
    assert saved['fields']['客户名称'] == '人工修正客户'
    assert saved['human_note'] == '人工备注'
    assert saved['review_status'] == '确认通过'
    assert saved['processing_artifacts']['seals'][0]['returned_image_status'] == 'downloaded'


def test_replaced_stamp_does_not_start_old_download(images):
    complete(images, images.image_job)
    saved = result(images)
    # Simulate a new run that has already replaced every old request identity.
    with images.store.database.connect() as con:
        encoded = json.dumps(saved, ensure_ascii=False).replace('seal-stamp-', 'new-seal-stamp-')
        con.execute('UPDATE results SET result_json=? WHERE id=?', (encoded, saved['id']))
    assert images.worker.images.step()
    assert images.downloads == []


def test_image_evidence_does_not_invalidate_an_already_queued_retry(images):
    complete(images, images.image_job)
    saved = result(images)
    task = images.store.create_retries('retry-images', [saved['id']], {saved['id']: {
        'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
        '_independent_dzt': True, '_execution_scope': 'local',
        'recognition_config': {'fields': ['paddle_v6']}}})
    retry = images.store.claim()
    assert retry['id'] == task['items'][0]['id']
    assert images.worker.images.step()
    images.store.finish(retry, {'overall': '通过', 'review_status': '无需复核',
        'fields': {'客户名称': '重新识别客户'}})
    assert images.store.get(retry['id'])['status'] == 'succeeded'
    assert result(images)['fields']['客户名称'] == '重新识别客户'


def test_deleted_result_is_not_downloaded_or_recreated(images):
    complete(images, images.image_job)
    saved = result(images)
    images.store.database.delete_results([saved['id']])
    assert not images.worker.images.step()
    assert images.downloads == []
    assert images.store.database.list_results() == []
