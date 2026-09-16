const assert = require('node:assert/strict');
const test = require('node:test');
const {providerErrors} = require('../static/modules/provider_errors.mjs');
const {workbenchAttention} = require('../static/modules/progress_presentation.mjs');
const {createRecords} = require('../static/modules/records.mjs');
const {createReview} = require('../static/modules/review.mjs');
const workbench = require('../static/workbench.js');
const {providerProgress} = require('../static/modules/provider_progress.mjs');

const failure = '单证通文件上传失败：HTTP 503；服务暂不可用 <详情> "重试"';
const record = () => ({id: 2841, filename: '7273687654.jpg', fields: {},
  date_check: {status: '缺少比对依据'}, seal_check: {status: '缺少比对依据'},
  overall: '需人工复核', review_status: '待复核',
  recognition_variants: Object.fromEntries(['fields', 'handwriting', 'date', 'seal'].map(stage =>
    [stage, [{method: 'danzhengtong', error: failure}]])),
  review_reasons: [`印刷字段：danzhengtong：${failure}`, `签收日期：danzhengtong：${failure}`]});

function ui() {
  const nodes = new Map();
  return {$: selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value: '', innerHTML: '', dataset: {}, classList: {toggle() {}}});
    return nodes.get(selector);
  }, $$: () => [], toast() {}};
}

test('a cached upload failure shared by four stages is displayed once', () => {
  assert.deepEqual(providerErrors(record()), [failure]);
  assert.deepEqual(providerErrors({review_reasons: [`单证通 dAnZhEnGtOnG: ${failure}`]}), [failure]);
  assert.deepEqual(providerErrors({error_message: failure}), [failure]);
  assert.deepEqual(providerErrors({}), []);
});

test('record list shows saved provider error even without a top-level error and escapes its text', () => {
  const controller = createRecords({environment: {}, ui: ui(), recordsState: {selected: new Set()}, ReceiptWorkbench: workbench});
  const markup = controller.recordRow(record());
  assert.equal((markup.match(/class="error-text"/g) || []).length, 1);
  assert.match(markup, /单证通文件上传失败：HTTP 503/);
  assert.match(markup, /&lt;详情&gt; &quot;重试&quot;/);
  assert(!markup.includes('<详情>'));
});

test('workbench prioritizes the actual provider failure over missing comparison requirements', () => {
  const attention = workbenchAttention({status: 'completed', record: record()}, {workbench});
  assert.equal(attention.primary, failure);
  assert.equal(attention.danger, true);
  assert.equal(workbench.category({status: 'succeeded', record: record(), review_status: '待复核'}), 'failed');
});

test('provider timeouts and connection errors are failures with a retry action', () => {
  for (const error_message of [
    '单证通等待识别结果超时（60 秒），已停止查询；reqUuid=request',
    "单证通查询结果失败：ReadTimeout: HTTPSConnectionPool(host='api.sinotrans.com'): Read timed out.",
    "单证通文件上传失败：ConnectionError: Max retries exceeded; nodename nor servname provided",
  ]) {
    const timedOut = {...record(), recognition_variants: {}, review_reasons: [], error_message};
    assert.equal(workbench.category({status: 'succeeded', record: timedOut}), 'failed');
    const controller = createRecords({environment: {}, ui: ui(), recordsState: {selected: new Set()}, ReceiptWorkbench: workbench});
    const markup = controller.recordRow(timedOut);
    assert.match(markup, /class="pill danger">识别失败<\/span>/);
    assert.match(markup, /data-retry-row="2841">重试<\/button>/);
    assert.doesNotMatch(markup, /data-open-review="2841">复核<\/button>/);
  }
});

test('processing workbench puts actively running receipts first', () => {
  const items = [
    {id: 'ready', status: 'ready'},
    {id: 'paused', status: 'queued'},
    {id: 'running', status: 'running'},
    {id: 'upload', status: 'awaiting_upload'},
  ];
  assert.deepEqual(workbench.ordered(items).map(item => item.id), ['running', 'paused', 'upload', 'ready']);
});

test('unconfirmed machine mismatches stay in the review category', () => {
  assert.equal(workbench.category({status: 'succeeded', review_status: '无需复核', final_result: '不通过',
    date_check: {status: '不匹配'}, seal_check: {status: '匹配'}}), 'review');
  assert.equal(workbench.category({status: 'succeeded', review_status: '确认不通过', final_result: '不通过',
    date_check: {status: '不匹配'}, seal_check: {status: '匹配'}}), 'rejected');
});

test('review summary exposes the provider failure first without duplicating stage reasons', () => {
  const surface = ui();
  const controller = createReview({environment: {}, ui: surface,
    reviewState: {current: record()}, ReceiptWorkbench: workbench});
  controller.syncReviewReadiness();
  const markup = surface.$('[data-status-summary]').innerHTML;
  assert.match(markup, /review-issue-list"><button[^>]+data-issue-target="evidence"/);
  assert.equal((markup.match(/<span>单证通文件上传失败/g) || []).length, 1);
  assert.match(markup, /&lt;详情&gt; &quot;重试&quot;/);
  assert(!markup.includes('<详情>'));
});

test('running jobs display the provider stage, wait duration and query count without an invented percentage', () => {
  const job = {status: 'running', progress: {provider: 'danzhengtong', stage: 'waiting',
    elapsed_seconds: 42, timeout_seconds: 60, poll_count: 15}};
  const attention = workbenchAttention({status: 'running', job}, {workbench});
  assert.equal(attention.primary, '单证通：等待识别结果');
  assert.match(attention.secondary, /已等待 42 秒/);
  assert.match(attention.secondary, /等待上限 1 分钟/);
  assert.match(attention.secondary, /已查询 15 次/);
  assert(!attention.secondary.includes('%'));
  assert.equal(providerProgress(job, {paused: true}).primary, '单证通：正在暂停查询');
  assert.match(providerProgress(job, {paused: true}).secondary, /不再发起后续查询/);
  const paused = providerProgress({...job, progress: {...job.progress, stage: 'paused'}}, {paused: true});
  assert.equal(paused.primary, '单证通：已暂停查询');
  assert.match(paused.secondary, /暂停期间不查询，总等待时间不重置/);
  assert.equal(providerProgress({...job, status: 'queued'}), null);
  assert.equal(providerProgress({status: 'running'}), null);
  for (const [stage, title] of [['uploading', '上传文件'], ['submitting', '提交识别'], ['completed', '结果已返回']]) {
    const state = providerProgress({...job, progress: {...job.progress, stage}});
    assert.equal(state.primary, `单证通：${title}`);
  }
  const failed = providerProgress({...job, progress: {...job.progress, stage: 'failed', error_message: failure}});
  assert(failed.danger);
  assert(failed.secondary.includes(failure));
  assert.match(providerProgress({...job, progress: {...job.progress, simulated: true}}).primary, /单证通（模拟）/);
});
