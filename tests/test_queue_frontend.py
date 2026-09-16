from pathlib import Path
import shutil
import subprocess

import pytest


def test_progress_projection_handles_retries_and_status_result_races():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script = '''
const assert = require('node:assert/strict');
const q = require('./static/queue_state.js');
const records = [{id:1,filename:'same.jpg',overall:'通过'}, {id:2,filename:'same.jpg',overall:'通过'}];
const jobs = [{id:'retry',target_result_id:1,filename:'same.jpg',status:'running'},
              {id:'original',result_id:1,filename:'same.jpg',status:'succeeded'},
              {id:'upload',filename:'same.jpg',status:'awaiting_upload'}];
const rows = q.rows(records, jobs), counts = q.counts(rows);
assert.equal(rows.length, 3);
assert.equal(counts.running, 1);
assert.equal(counts.awaiting_upload, 1);
assert.equal(counts.completed, 1);
assert.equal(q.counts([]).status, '暂无回单');
assert.equal(q.counts([{status:'cancelled'}]).completed, 1);
const raced = q.rows([{id:3,filename:'new.jpg',queue:{job_id:'new'},overall:'通过'}],
                     [{id:'new',filename:'new.jpg',status:'running'}]);
assert.equal(raced.length, 1);
assert.equal(raced[0].status, 'running');
'''
    completed = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_workbench_keeps_processing_separate_from_verdict_and_skips_deferred_reviews():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script = '''
const assert = require('node:assert/strict');
const w = require('./static/workbench.js');
const q = require('./static/queue_state.js');
const record = {id:1, filename:'a.jpg', review_status:'确认通过', final_result:'通过', fields:{客户名称:'客户'}};
const running = q.rows([record], [{id:'retry', target_result_id:1, status:'running', filename:'a.jpg', final_result:'通过'}])[0];
assert.equal(w.category(running), 'processing');
assert.equal(w.matches(running, 'passed'), false);
assert.equal(running.record.fields.客户名称, '客户');
assert.equal(w.category({status:'failed',review_status:'待复核',final_result:'不通过'}), 'failed');
const completed = q.rows([record], [{id:'old',result_id:1,status:'succeeded',review_status:'待复核',final_result:'需人工复核'}])[0];
assert.equal(w.category(completed), 'passed');
assert.equal(w.nextReview([{id:1},{id:2}], 2, new Set([1])), undefined);
assert.equal(w.nextReview([{id:1},{id:2},{id:3}], 2, new Set([1])).id, 3);
assert.deepEqual(w.issues({date_check:{reliable:true,status:'匹配'},seal_check:{reliable:true,status:'匹配'},field_metadata:{仓库接收人:{low_confidence:true}},review_reasons:['签收填写内容需人工确认','存在低置信度字段']}), [{target:'handwriting',message:'签收填写内容需人工确认'}]);
const sources = w.imageSources({preview_url:'/files/previews/a.jpg',processing_artifacts:{date:[{original_url:'/files/artifacts/date.jpg'},{original_url:'/files/artifacts/date.jpg'}],seals:[{original_url:'javascript:alert(1)'}]}});
assert.equal(sources.length, 2);
assert.equal(sources[1].group, 'date');
assert.deepEqual(w.issues({date_check:{reliable:true,status:'匹配'},seal_check:{reliable:true,status:'匹配'},fields:{实收数量:'',拒收数量:''},review_reasons:[]}), []);
'''
    completed = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_review_date_requires_confirmation_and_preserves_required_calendar_start():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script = '''
const assert = require('node:assert/strict');
const w = require('./static/workbench.js');
const missing = w.dateReview('2025-03-31', '');
assert.equal(missing.actual, '');
assert.equal(missing.choice, '');
assert.equal(missing.draft, '2025-03-31');
assert.equal(w.chooseDate(missing, 'same').actual, '2025-03-31');
const different = w.chooseDate(missing, 'different');
assert.equal(different.draft, '2025-03-31');
assert.equal(different.actual, '');
assert.equal(w.chooseDate(different, 'edit', '2025-03-31').actual, '');
assert.equal(w.chooseDate(different, 'edit', '2025-04-01').actual, '2025-04-01');
assert.equal(w.chooseDate(different, 'edit', '2025-02-30').actual, '');
assert.equal(w.dateReview('', '').draft, '');
assert.equal(w.dateReview('2025-02-30', '').required, '');
assert.equal(w.dateReview('2025-03-31', '2025-04-01').actual, '2025-04-01');
assert.equal(w.checkStatus({seal_check:{status:'匹配',reliable:true}}, 'seal'), '匹配');
assert.equal(w.checkStatus({seal_check:{status:'匹配',reliable:false}}, 'seal'), '匹配待确认');
assert.equal(w.checkStatus({}, 'seal'), '结果未加载');
assert.equal(w.issues({date_check:{status:'匹配',reliable:true},seal_check:{status:'匹配',reliable:false}})[0].message, '客户印章比对匹配，但证据不足，仍需确认');
'''
    completed = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
