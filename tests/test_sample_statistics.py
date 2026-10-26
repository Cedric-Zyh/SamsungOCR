import json
from copy import deepcopy

import pytest

from receipt_ocr.web.sample_statistics import evaluate_sample, summarize_problem_annotations
from receipt_ocr.web.samples import SampleCenter
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from test_sample_center import samples


TRUTH = {"fields": {"客户名称": "客户甲", "签章要求": "客户甲专用章"},
         "actual_date": "2025-02-13", "seal_should_match": True}
PLAN = {"date": ["paddle_v6"], "seal": ["qingtong", "paddle_v6"]}


def job(result, *, plan=PLAN, status="succeeded"):
    return {"status": status, "result_id": 7, "result_json": json.dumps(result),
            "payload_json": json.dumps({"sample_options": {"recognition_config": plan}})}


def reading(status="匹配", value="客户甲专用章"):
    return {"status": status, "recognized": value}


def remote(template="匹配", ocr="不匹配"):
    return {"method": "qingtong", "details": {"seal_check": {
        # A merged successful verdict must not conceal an OCR failure.
        "status": "匹配", "dual_check": {"selected": {
            "template": {"label": "客户甲专用章", "requirement_match": {"status": template}},
            "ocr": {"text": "客户乙专用章", "comparison": {"status": ocr}},
        }}}}}


def test_channels_are_independent_and_do_not_inherit_merged_verdict():
    result = {"seal_check": reading(), "date_check": {"actual": "2025年2月13日", "status": "不匹配"},
              "recognition_variants": {"seal": [
                  remote(), {"method": "paddle_v6", "details": {"seal_check": reading("部分匹配", "客户甲")}},
                  {"method": "paddle_seal", "details": {"seal_check": reading()}}]}}
    plan = {**PLAN, "seal": ["qingtong", "paddle_v6", "paddle_seal"]}
    row = evaluate_sample("one.png", TRUTH, job(result, plan=plan))
    assert row["date"]["state"] == "正确"  # Date value, not business matching status.
    assert row["seals"]["qingtong_template"]["state"] == "正确"
    assert row["seals"]["qingtong_ocr"]["state"] == "错误"
    assert row["seals"]["local:paddle_v6"]["state"] == "错误"
    assert row["seals"]["local:paddle_seal"]["state"] == "正确"
    assert row["seals"]["local:paddle_v6"]["value"] == "客户甲"


def test_negative_truth_is_a_decision_annotation_not_an_actual_stamp_transcription():
    truth = {**TRUTH, "seal_should_match": False}
    row = evaluate_sample("one.png", truth, job({"recognition_variants": {"seal": [remote()]}}))
    assert row["expected_seal"] == "不匹配"
    assert row["seals"]["qingtong_template"]["state"] == "错误"
    assert row["seals"]["qingtong_ocr"]["state"] == "正确"


def test_missing_evidence_is_not_a_correct_negative_and_absent_date_is_valid():
    truth = {**TRUTH, "seal_should_match": False, "date_present": False, "actual_date": ""}
    row = evaluate_sample("one.png", truth, job({
        "date_check": {"actual": "", "status": "未识别"},
        "recognition_variants": {"seal": [{"method": "paddle_v6", "details": {
            "seal_check": reading("不匹配", "")}}]}}))
    assert row["date"]["state"] == "正确"
    assert row["seals"]["local:paddle_v6"]["state"] == "未识别"
    assert row["seals"]["qingtong_template"]["state"] == "未识别"


def test_unlabeled_disabled_pending_failure_and_no_job_are_distinct():
    result = {"date_check": {"actual": "2025-02-13"}}
    assert evaluate_sample("one.png", {}, job(result))["date"]["state"] == "待标注"
    assert evaluate_sample("one.png", TRUTH, job(result, status="running"))["date"]["state"] == "测试中"
    assert evaluate_sample("one.png", TRUTH, {})["date"]["state"] == "未测试"
    failed = evaluate_sample("one.png", TRUTH, job({}, plan={"date": [], "seal": ["qingtong"]}, status="failed"))
    assert failed["date"]["state"] == "未运行"
    assert failed["seals"]["qingtong_template"]["state"] == "失败"
    assert failed["seals"]["qingtong_ocr"]["state"] == "失败"
    assert failed["seals"]["local:legacy_local"]["state"] == "未运行"


def test_failed_provider_does_not_take_successful_other_provider_value():
    result = {"seal_check": reading(), "recognition_variants": {"seal": [
        {"method": "qingtong", "error": "超时", "details": None},
        {"method": "paddle_v6", "details": {"seal_check": reading()}},
    ]}}
    row = evaluate_sample("one.png", TRUTH, job(result))
    assert row["seals"]["qingtong_ocr"]["state"] == "失败"
    assert row["seals"]["qingtong_ocr"]["value"] == ""
    assert row["seals"]["local:paddle_v6"]["state"] == "正确"


def test_legacy_dual_and_local_channel_stay_separate():
    result = deepcopy(remote()["details"])
    result["seal_check"]["local_channel"] = reading("不匹配", "另一公司")
    legacy = {"status": "succeeded", "result_json": json.dumps(result), "payload_json": "{}"}
    row = evaluate_sample("one.png", TRUTH, legacy)
    assert row["seals"]["qingtong_template"]["state"] == "正确"
    assert row["seals"]["qingtong_ocr"]["state"] == "错误"
    assert row["seals"]["local:legacy_local"]["state"] == "错误"


def test_human_problem_markers_belong_to_the_selected_test_and_never_change_evaluation():
    result = {'date_check': {'actual': '2025-02-13'}, 'recognition_variants': {'seal': [remote()]}}
    original = evaluate_sample('one.png', TRUTH, job(result))
    truth = {**TRUTH, 'human_note': '原图有遮挡', 'sample_features': ['遮挡'],
             'problem_reviews': {'7': {'status': 'issues', 'types': ['日期读错', '印章判定错误']},
                                 '8': {'status': 'clear', 'types': []}}}
    current = evaluate_sample('one.png', truth, job(result))
    assert current['problem_review']['status'] == 'issues'
    assert current['problem_review']['types'] == ['日期读错', '印章判定错误']
    assert current['human_note'] == '原图有遮挡'
    assert current['sample_features'] == ['遮挡']
    assert current['date'] == original['date']
    assert current['seals'] == original['seals']
    later = evaluate_sample('one.png', truth, {**job(result), 'result_id': 8})
    assert later['problem_review']['status'] == 'clear'
    assert later['problem_review']['types'] == []
    unmarked = evaluate_sample('one.png', truth, {**job(result), 'result_id': 9})
    assert unmarked['problem_review']['status'] == 'unmarked'
    assert unmarked['problem_review']['types'] == []
    assert unmarked['sample_features'] == ['遮挡']


def test_human_problem_distribution_counts_samples_and_unique_tags_separately():
    first = {'filename': 'one.png', 'problem_review': {'status': 'issues', 'types': ['日期读错', '日期读错', '印章判定错误']},
             'sample_features': ['遮挡', '遮挡'], 'legacy_problem_types': ['旧印章问题']}
    second = {'filename': 'two.png', 'problem_review': {'status': 'clear', 'types': ['不应计入']}}
    summary = summarize_problem_annotations([first, first, second, {'filename': 'three.png'}])
    assert summary['total'] == 3
    assert summary['statuses'] == {'unmarked': 1, 'clear': 1, 'issues': 1}
    assert {item['type']: item['count'] for item in summary['types']} == {'日期读错': 1, '印章判定错误': 1}
    assert summary['features'] == [{'type': '遮挡', 'count': 1}]
    assert summary['legacy_samples'] == 1
    assert summary['legacy_types'] == [{'type': '旧印章问题', 'count': 1}]


def test_remote_seal_marker_does_not_turn_a_correct_final_or_local_result_into_an_error():
    result = {'seal_check': reading(), 'recognition_variants': {'seal': [
        remote(template='不匹配', ocr='不匹配'),
        {'method': 'paddle_v6', 'details': {'seal_check': reading()}},
    ]}}
    before = deepcopy(result)
    baseline = evaluate_sample('one.png', TRUTH, job(result))
    truth = {**TRUTH, 'problem_reviews': {'7': {
        'status': 'issues', 'types': ['清瞳印章判定错误'],
    }}}
    marked = evaluate_sample('one.png', truth, job(result))
    assert marked['problem_review']['types'] == ['清瞳印章判定错误']
    assert marked['seals']['qingtong_template']['state'] == '错误'
    assert marked['seals']['qingtong_ocr']['state'] == '错误'
    assert marked['seals']['local:paddle_v6']['state'] == '正确'
    assert marked['seals'] == baseline['seals']
    assert result == before and result['seal_check']['status'] == '匹配'


def test_channel_specific_and_legacy_seal_labels_stay_separate_in_manual_counts():
    both = {'filename': 'one.png', 'problem_review': {'status': 'issues', 'types': [
        '清瞳印章判定错误', '本地印章判定错误', '清瞳印章判定错误',
    ]}}
    summary = summarize_problem_annotations([both, both])
    assert summary['total'] == 1
    assert summary['statuses']['issues'] == 1
    assert {item['type']: item['count'] for item in summary['types']} == {
        '清瞳印章判定错误': 1, '本地印章判定错误': 1,
    }
    old = {'filename': 'old.png', 'problem_review': {'status': 'issues', 'types': ['印章判定错误']}}
    with_old = summarize_problem_annotations([both, old])
    assert with_old['statuses']['issues'] == 2
    assert {item['type']: item['count'] for item in with_old['types']} == {
        '清瞳印章判定错误': 1, '本地印章判定错误': 1, '印章判定错误': 1,
    }


def test_statistics_latest_batch_scope_and_production_isolation(samples):
    ctx, client = samples
    truth = json.loads(ctx.GROUND_TRUTH_PATH.read_text())
    truth["one.png"].update(TRUTH)
    ctx.GROUND_TRUTH_PATH.write_text(json.dumps(truth))
    ctx.database.insert_result(filename="one.png", stored_name="one.png", preview_name="", task_id="",
                               result={"date_check": {"actual": "2025-02-13"}})
    assert all(row["date"]["state"] == "未测试" for row in client.get("/api/samples/statistics").json["items"])
    first = client.post("/api/samples/run", json={"filenames": ["one.png"]}).json["task_id"]
    service = SampleCenter(ctx)
    claimed = service.store.claim()
    service.store.finish(claimed, {"date_check": {"actual": "2025-02-13"}, "seal_check": reading()})
    first_id = client.get("/api/samples/statistics").json["items"][0]["result_id"]
    second = client.post("/api/samples/run", json={"filenames": ["one.png"]}).json["task_id"]
    queued = client.get("/api/samples/statistics").json
    assert len(queued["items"]) == 4
    assert queued["items"][0]["date"]["state"] == "测试中"
    service.store.finish(service.store.claim(), error="整次测试失败")
    latest = client.get("/api/samples/statistics").json
    assert latest["items"][0]["date"]["state"] == "失败"
    old = client.get("/api/samples/statistics", query_string={"task_id": first}).json
    assert len(old["items"]) == 1
    assert old["items"][0]["date"]["state"] == "正确"
    assert old["items"][0]["result_id"] == first_id
    assert {row["id"] for row in latest["batches"]} == {first, second}
    assert client.get("/api/samples/statistics?task_id=unknown").status_code == 404
    assert len(ctx.database.list_results()) == 1
    assert ctx.database.list_tasks() == []


def test_statistics_preserves_each_jobs_origin_and_batch_scheme(samples):
    ctx, client = samples
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    settings['recognition_config']['seal'] = ['paddle_v6']
    ctx.database.set_json_setting('recognition_defaults', settings)
    first = client.post('/api/samples/run', json={'filenames': ['one.png', 'two.png']}).json['task_id']
    service = SampleCenter(ctx)
    service.store.finish(service.store.claim(), {})
    service.store.finish(service.store.claim(), error='测试失败')
    finished = '2026-10-23T23:39:17+08:00'
    started = '2026-10-23T23:38:00+08:00'
    with service.database.connect() as con:
        con.execute('UPDATE recognition_jobs SET started_at=?,finished_at=? WHERE task_id=?',
                    (started, finished, first))
    settings['recognition_config']['seal'] = ['paddle_seal']
    ctx.database.set_json_setting('recognition_defaults', settings)
    second = client.post('/api/samples/run', json={'filenames': ['one.png']}).json['task_id']
    data = client.get('/api/samples/statistics').json
    rows = {row['filename']: row for row in data['items']}
    assert rows['one.png']['task_id'] == second
    assert rows['two.png']['task_id'] == first
    assert rows['two.png']['task_name'] == '样单测试 · 2 张'
    assert rows['two.png']['tested_at'] == finished  # Keep the original time zone.
    assert rows['blank.png']['tested_at'] == rows['blank.png']['task_id'] == ''
    batches = {batch['id']: batch for batch in data['batches']}
    assert batches[first]['status'] == '已完成'
    assert batches[first]['completed'] == 2
    assert batches[first]['succeeded'] == batches[first]['failed'] == 1
    assert batches[first]['status_counts'] == {'succeeded': 1, 'failed': 1}
    assert batches[first]['recognition_config']['seal'] == ['paddle_v6']
    assert batches[first]['scheme_labels']['seal'] == ['PaddleOCR PP-OCRv6 Small']
    assert batches[first]['started_at'] == started
    assert batches[first]['finished_at'] == finished
    assert batches[second]['recognition_config']['seal'] == ['paddle_seal']
    assert batches[second]['finished_at'] == ''
    assert batches[second]['status_counts'] == {'queued': 1}
    scoped = client.get('/api/samples/statistics', query_string={'task_id': first}).json
    assert {row['task_id'] for row in scoped['items']} == {first}
    assert {row['tested_at'] for row in scoped['items']} == {finished}
