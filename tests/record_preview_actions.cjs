const test = require('node:test');
const assert = require('node:assert/strict');
const {createRecordRow} = require('../static/modules/records/record_row.mjs');
const {ReceiptWorkbench} = require('../static/modules/core/workbench.mjs');
const row = createRecordRow({ReceiptWorkbench, recordsState: {selected: new Set()}});

test('uploaded, unrecognized receipts expose separate preview and start actions', () => {
  const markup = row({filename: 'same.jpg', processing_status: 'ready', processing_job: {id: 'job-1'}});
  assert.match(markup, /data-preview-job="job-1" >预览/);
  assert.match(markup, /data-job-start="job-1">开始识别/);
  assert.doesNotMatch(markup, /data-open-review/);
});

test('completed receipts, including those awaiting review, open the existing read-only viewer', () => {
  for (const review_status of ['无需复核', '待复核', '确认通过']) {
    const markup = row({id: 7, filename: 'done.jpg', review_status});
    assert.match(markup, /data-open-review="7" data-review-mode="view">查看/);
    assert.doesNotMatch(markup, /data-preview-job|>复核<\/button>|>开始识别/);
  }
});

test('missing uploads explain why preview is unavailable and retain the upload action', () => {
  const markup = row({filename: 'same.jpg', processing_status: 'awaiting_upload', processing_job: {id: 'missing'}});
  assert.match(markup, /data-preview-job="missing" disabled title="图片尚未上传，请先补传图片"/);
  assert.match(markup, /data-job-upload="missing">补传图片/);
});
