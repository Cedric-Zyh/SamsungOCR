const assert = require('node:assert/strict');
const test = require('node:test');
const {createProgress} = require('../static/modules/workbench/workbench.mjs');
const {createState} = require('../static/modules/core/state.mjs');
const {workbenchAttention} = require('../static/modules/workbench/presentation.mjs');
const ReceiptWorkbench = require('../static/modules/core/workbench.mjs').ReceiptWorkbench;
const ReceiptQueue = require('../static/modules/core/queue.mjs').ReceiptQueue;

function node(dataset = {}) {
  const events = new Map(), classes = new Set();
  return {dataset, attributes: {}, style: {}, value: '', textContent: '', innerHTML: '', disabled: false,
    parentElement: {setAttribute() {}}, files: [],
    classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name)},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(type, callback) { if (!events.has(type)) events.set(type, []); events.get(type).push(callback); },
    async dispatch(type, target = this) { for (const callback of events.get(type) || []) await callback({target}); },
    closest(selector) {
      return selector.split(',').some(part => {
        const key = part.trim().replace(/^\[data-|\]$/g, '').replace(/-([a-z])/g, (_, char) => char.toUpperCase());
        return key in this.dataset;
      }) ? this : null;
    }, scrollTo() {}, click() {},
  };
}

const row = (id, extra = {}) => ({id, filename: `receipt-${id}.jpg`, fields: {客户名称: '测试客户'},
  review_status: '待复核', date_check: {status: '匹配', reliable: true}, seal_check: {status: '匹配', reliable: false}, ...extra});
const item = record => ({id: record.id, filename: record.filename, status: 'succeeded', review_status: record.review_status, final_result: record.final_result, record});
const attention = record => workbenchAttention(item(record), {workbench: ReceiptWorkbench});
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };

function harness() {
  const nodes = new Map(), requests = [], notices = [], scopes = [], files = [];
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); };
  const filters = ['completed', 'processing', 'failed'].map(workFilter => node({workFilter}));
  const counts = ['completed', 'processing', 'failed'].map(workCount => node({workCount}));
  const state = createState(), h = {data: [row(1)], jobs: [], control: {paused: false, running: 0, queued: 0}, confirm: false};
  h.api = async (url, options) => {
    if (options?.method) throw Error(`Unexpected write ${url}`);
    if (url === '/api/queue/control') return h.control;
    if (url.startsWith('/api/queue?')) return h.jobs;
    if (url.startsWith('/api/daily-results?')) return h.data;
    throw Error(url);
  };
  const environment = {document: {body: {dataset: {activePage: 'progress'}}, createElement() { const input = node(); files.push(input); return input; }},
    window: {confirm: () => h.confirm}, location: {}, localStorage: {getItem: () => '2026-09-10', setItem() {}},
    FormData: class { append() {} },
  };
  const controller = createProgress({environment, ui: {$, $$: selector => selector === '[data-work-filter]' ? filters : selector === '[data-work-count]' ? counts : [], toast: message => notices.push(message)},
    ...Object.fromEntries(Object.entries(state).map(([key, value]) => [`${key}State`, value])), ReceiptWorkbench, ReceiptQueue,
    api: async (url, options) => { requests.push({url, options}); return h.api(url, options); },
    retryOne: async (...args) => { h.retry = args; },
    openResultPreview: async (...args) => { h.preview = args; },
    startReviewScope: async (...args) => { scopes.push(args); return h.openReview?.(); },
  });
  controller.initialize();
  return Object.assign(h, {$, state, filters, counts, requests, notices, scopes, files, environment, controller});
}

test('view result opens the selected preview without navigating to review', async () => {
  const h = harness(), button = node({workView: '7'});
  await h.$('#queue').dispatch('click', button);
  assert.deepEqual(h.preview, [7, button]);
  assert.equal(h.environment.location.hash, undefined);
  h.preview = null; button.disabled = true;
  await h.$('#queue').dispatch('click', button);
  assert.equal(h.preview, null);
});

test('workbench shows active jobs, hides completed and offers review through records', async () => {
  const h = harness();
  h.data = [row(1), row(2, {review_status: '已复核', final_result: '通过'})];
  h.jobs = [{id: 'cancelled', filename: 'cancelled.jpg', status: 'cancelled'}, {id: 'waiting', filename: 'waiting.jpg', status: 'queued'}];
  assert.equal(h.state.progress.workFilter, 'all');
  await h.controller.loadDailyResults();
  assert.match(h.$('#queue').innerHTML, /waiting.jpg/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /receipt-1|receipt-2|cancelled.jpg/);
  h.$('#workbench-needs-review').checked = true;
  await h.$('#workbench-needs-review').dispatch('change');
  assert.match(h.$('#queue').innerHTML, /receipt-1.jpg/);
  assert.match(h.$('#queue').innerHTML, /data-work-view="1"/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /data-work-review/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /receipt-2.jpg|waiting.jpg/);
  assert.equal(h.$('#workbench-visible-count').textContent, '待复核 1 张');
  await h.filters[0].dispatch('click');
  assert.equal(h.$('#workbench-needs-review').checked, false);
  assert.match(h.$('#queue').innerHTML, /receipt-2.jpg/);
  h.$('#workbench-needs-review').checked = true;
  await h.$('#workbench-needs-review').dispatch('change');
  h.$('#workbench-needs-review').checked = false;
  await h.$('#workbench-needs-review').dispatch('change');
  assert.match(h.$('#queue').innerHTML, /waiting.jpg/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /receipt-1.jpg|receipt-2.jpg/);
});

test('workbench polling updates provider progress while the record result remains cached', async () => {
  const h = harness();
  h.state.progress.workFilter = 'processing';
  h.jobs = [{id: 'in-flight', filename: 'waiting.jpg', status: 'running',
    progress: {provider: 'danzhengtong', stage: 'uploading', elapsed_seconds: 2}}];
  await h.controller.loadDailyResults();
  assert.match(h.$('#queue').innerHTML, /单证通：上传文件/);
  const requests = h.requests.filter(r => r.url.startsWith('/api/daily-results')).length;
  h.jobs[0].progress = {provider: 'danzhengtong', stage: 'waiting', elapsed_seconds: 42,
    timeout_seconds: 60, poll_count: 15};
  await h.controller.loadDailyResults({refreshRecords: false});
  assert.match(h.$('#queue').innerHTML, /单证通：等待识别结果/);
  assert.match(h.$('#queue').innerHTML, /已等待 42 秒/);
  assert.equal(h.requests.filter(r => r.url.startsWith('/api/daily-results')).length, requests);
  h.control = {paused: true, status: 'pausing', running: 1, queued: 0};
  h.jobs[0].progress.stage = 'paused';
  await h.controller.loadDailyResults({refreshRecords: false});
  assert.match(h.$('#queue').innerHTML, /单证通：已暂停查询/);
  assert(!h.$('#queue').innerHTML.includes('status-pulse'));
  h.jobs[0].progress = {provider: 'danzhengtong', stage: 'failed', error_message: '<img onerror="alert(1)">接口失败'};
  await h.controller.loadDailyResults({refreshRecords: false});
  assert.match(h.$('#queue').innerHTML, /单证通：识别失败/);
  assert(!h.$('#queue').innerHTML.includes('<img'));
  assert.match(h.$('#queue').innerHTML, /&lt;img/);
});

test('seal-only work prioritizes the seal and keeps completed selections pending confirmation', () => {
  const base = row(1, {date_check: {status: '未执行', reliable: false}, review_reasons: ['部分识别：未执行项目不能据此判定整单通过']});
  assert.deepEqual(attention(base), {primary: '印章文字待确认', danger: false, secondary: '', secondaryDanger: undefined});
  const reliable = {...base, seal_check: {status: '匹配', reliable: true}};
  assert.equal(attention(reliable).primary, '已选内容识别完成，请确认结果');
  assert.equal(ReceiptWorkbench.issues(reliable).some(issue => issue.target === 'date'), true);
  assert.equal(ReceiptWorkbench.category(item(reliable)), 'review');
});

test('both mismatches remain visible and take priority over less serious review reasons', () => {
  const both = row(1, {date_check: {status: '不匹配', reliable: false}, seal_check: {status: '部分匹配', reliable: false}, review_reasons: ['字段缺失', '商品明细待确认']});
  const summary = attention(both);
  assert.equal(summary.primary, '签收日期与要求不一致');
  assert.equal(summary.secondary, '印章仅部分匹配，请确认 · 另有 2 项待确认');
  assert.equal(summary.danger, true); assert.equal(summary.secondaryDanger, true);
  assert.equal(attention({...both, date_check: {status: '未识别', reliable: false}}).primary, '印章仅部分匹配，请确认');
});

test('filenames, customers, errors, review text and job IDs remain escaped', async () => {
  const h = harness(), unsafe = '"><img src=x onerror=alert(1)>';
  h.state.progress.workNeedsReview = true;
  h.data = [row(1, {filename: unsafe, fields: {客户名称: unsafe}, date_check: {status: '匹配', reliable: true}, seal_check: {status: '匹配', reliable: true}, review_reasons: [unsafe]})];
  await h.controller.loadDailyResults();
  assert.doesNotMatch(h.$('#queue').innerHTML, /<img|<script/);
  assert.match(h.$('#queue').innerHTML, /&quot;&gt;&lt;img/);
  h.jobs = [{id: unsafe, filename: 'failed.jpg', status: 'failed', error_message: unsafe}];
  await h.controller.loadDailyResults(); await h.filters[2].dispatch('click');
  assert.match(h.$('#queue').innerHTML, /data-job-retry="&quot;&gt;&lt;img/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /<img/);
});

test('new imports appear first after polling while expanded list size is preserved', async () => {
  const h = harness(); h.state.progress.workNeedsReview = true; h.data = Array.from({length: 120}, (_, index) => row(index + 1));
  await h.controller.loadDailyResults();
  assert.equal((h.$('#queue').innerHTML.match(/data-work-view=/g) || []).length, 50);
  await h.$('#workbench-more').dispatch('click');
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 100 / 120 张');
  h.data = [row(121), ...h.data.slice().reverse()];
  await h.controller.loadDailyResults();
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 100 / 121 张');
  assert.equal(h.$('#queue').innerHTML.match(/data-work-view="(\d+)"/)[1], '121');
  await h.filters[1].dispatch('click'); await h.filters[0].dispatch('click');
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 50 / 121 张');
  await h.$('#workbench-more').dispatch('click');
  h.$('#progress-date').value = '2026-09-09'; await h.controller.loadDailyResults();
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 50 / 121 张');
});

test('date changes hide old rows and clear old progress; failed requests cannot start stale review', async () => {
  const h = harness(); await h.controller.loadDailyResults();
  const before = h.$('#queue').innerHTML;
  h.api = async () => { throw Error('offline'); };
  await assert.rejects(h.controller.loadDailyResults(), /offline/);
  assert.equal(h.$('#queue').innerHTML, before);
  assert.equal(h.$('#queue-start').disabled, true);
  assert.match(h.$('#progress-note').textContent, /上次成功加载/);
  const pending = deferred(); h.api = () => pending.promise;
  h.$('#progress-date').value = '2026-09-09'; const loading = h.controller.loadDailyResults();
  assert.equal(h.$('#task-section').classList.contains('hidden'), true);
  assert.equal(h.$('#task-summary').textContent, '正在加载');
  assert.equal(h.$('#task-progress').style.width, '0%');
  assert.equal(h.$('#workbench-total-label').textContent, '');
  pending.reject(Error('offline')); await assert.rejects(loading, /offline/);
  assert.equal(h.$('#task-summary').textContent, '暂时无法加载');
  await h.$('#workbench-start-review').dispatch('click'); assert.equal(h.scopes.length, 0);
});

test('remote retry respects confirmation and switches to processing only after success', async () => {
  const h = harness(), retry = deferred();
  h.jobs = [{id: 'remote/1', filename: 'remote.jpg', status: 'failed', uses_remote: true}];
  await h.controller.loadDailyResults(); await h.filters[2].dispatch('click');
  const button = node({jobRetry: 'remote/1'});
  await h.$('#queue').dispatch('click', button);
  assert.equal(h.requests.some(request => request.options?.method), false);
  h.confirm = true; const read = h.api;
  h.api = async (url, options) => options?.method ? retry.promise : read(url, options);
  const retrying = h.$('#queue').dispatch('click', button);
  await h.$('#queue').dispatch('click', node({jobRetry: 'remote/1'}));
  assert.equal(h.requests.filter(request => request.options?.method).length, 1);
  assert.equal(h.requests.at(-1).url, '/api/jobs/remote%2F1/retry');
  assert.equal(h.state.progress.workFilter, 'failed');
  h.jobs[0].status = 'queued'; retry.resolve({}); await retrying;
  assert.equal(h.state.progress.workFilter, 'processing');
  assert.match(h.$('#queue').innerHTML, /等待后台识别/);
});

test('replenishing an image prevents duplicate uploads and recovers cancelled file selection', async () => {
  const h = harness(), upload = deferred();
  h.jobs = [{id: 'upload-1', filename: 'upload.jpg', status: 'awaiting_upload'}];
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  const button = node({jobUpload: 'upload-1'});
  await h.$('#queue').dispatch('click', button);
  await h.$('#queue').dispatch('click', node({jobUpload: 'upload-1'}));
  assert.equal(h.files.length, 1);
  await h.files[0].dispatch('cancel'); assert.equal(button.disabled, false);
  await h.$('#queue').dispatch('click', button); const input = h.files[1];
  const read = h.api; h.api = async (url, options) => options?.method ? upload.promise : read(url, options);
  input.files = [{}]; const uploading = input.dispatch('change');
  await h.$('#queue').dispatch('click', node({jobUpload: 'upload-1'}));
  assert.equal(h.requests.filter(request => request.options?.method).length, 1);
  assert.match(h.$('#queue').innerHTML, /正在上传图片/);
  assert.match(h.$('#progress-note').textContent, /保持页面打开/);
  h.jobs[0].status = 'queued'; upload.resolve({}); await uploading;
  assert.equal(h.state.imports.uploadingJobs.size, 0);
  assert.equal(h.state.progress.workPendingJobs.size, 0);
  assert.equal(h.$('#progress-note').textContent, '');
});

test('withdrawing a queued task keeps it available for explicit restart', async () => {
  const h = harness(); h.data = []; h.jobs = [{id: 'queue-1', filename: 'keep.jpg', status: 'queued', start_requested: true}];
  await h.controller.loadDailyResults();
  assert.match(h.$('#queue').innerHTML, /data-job-withdraw="queue-1"/);
  const read = h.api;
  h.api = async (url, options) => {
    if (options?.method) { h.jobs[0].status = 'ready'; h.jobs[0].start_requested = false; return {updated: 1}; }
    return read(url, options);
  };
  await h.$('#queue').dispatch('click', node({jobWithdraw: 'queue-1'}));
  const writes = h.requests.filter(request => request.options?.method);
  assert.deepEqual(writes.map(r => r.url), ['/api/jobs/manage']);
  assert.deepEqual(writes[0].options.json, {ids: ['queue-1'], action: 'withdraw'});
  assert.match(h.$('#queue').innerHTML, /data-job-start="queue-1"/);
});

test('completed receipts support select-all, indeterminate state, clear and scoped review', async () => {
  const h = harness(); h.state.progress.workNeedsReview = true;
  h.data = [row(1), row(2), row(3, {review_status: '确认通过', final_result: '通过'})];
  await h.controller.loadDailyResults();
  const all = h.$('#workbench-select-all');
  assert.equal(all.disabled, false);
  all.checked = true; await all.dispatch('change');
  assert.equal(all.checked, true); assert.equal(all.indeterminate, false);
  assert.equal(h.$('#queue-selection-count').textContent, '已选 2 张');
  assert.equal(h.$('#queue-selected-review').textContent, '复核所选（2）');
  assert.equal(h.$('#queue-selected-start').classList.contains('hidden'), true);
  const input = node({workSelect: 'record:1'}); input.checked = false;
  await h.$('#queue').dispatch('change', input);
  assert.equal(all.checked, false); assert.equal(all.indeterminate, true);
  await h.controller.loadDailyResults();
  assert.equal(h.$('#queue-selection-count').textContent, '已选 1 张');
  await h.$('#queue-selected-review').dispatch('click');
  assert.deepEqual(h.scopes[0], [{kind: 'selected', ids: [2], label: '工作台所选（1 张）'}, {recordId: 2}]);
  assert.equal(h.requests.some(request => request.options?.method), false);
  await h.$('#queue-selection-clear').dispatch('click');
  assert.equal(all.checked, false); assert.equal(all.indeterminate, false);
  assert.equal(h.$('#queue-selection').classList.contains('hidden'), true);
  all.checked = true; await all.dispatch('change');
  all.checked = false; await all.dispatch('change');
  assert.equal(h.$('#queue-selection').classList.contains('hidden'), true);
});

test('review selection is limited to displayed rows and resets with search and day changes', async () => {
  const h = harness(); h.state.progress.workNeedsReview = true;
  h.data = Array.from({length: 55}, (_, index) => row(index + 1));
  await h.controller.loadDailyResults();
  const all = h.$('#workbench-select-all'); all.checked = true; await all.dispatch('change');
  assert.equal(h.$('#queue-selection-count').textContent, '已选 50 张');
  await h.$('#queue-selected-review').dispatch('click');
  assert.equal(h.scopes[0][0].ids.length, 50);
  h.$('#workbench-search').value = 'receipt-1.jpg'; await h.$('#workbench-search').dispatch('input');
  assert.equal(all.checked, false);
  all.checked = true; await all.dispatch('change');
  h.$('#progress-date').value = '2026-09-09'; await h.controller.loadDailyResults();
  assert.equal(all.checked, false);
});

test('mixed selection separates record review from queued-job actions', async () => {
  const h = harness(); h.state.progress.workFilters = ['ready', 'queued', 'running', 'completed'];
  h.jobs = [readyJob('ready-1'), readyJob('queued-1', {status: 'queued'}), readyJob('running-1', {status: 'running'})];
  await h.controller.loadDailyResults();
  const all = h.$('#workbench-select-all'); all.checked = true; await all.dispatch('change');
  assert.equal(h.$('#queue-selection-count').textContent, '已选 3 张');
  assert.equal(h.$('#queue-selected-start').textContent, '开始所选（1）');
  assert.equal(h.$('#queue-selected-withdraw').textContent, '取消所选排队（1）');
  assert.equal(h.$('#queue-selected-review').textContent, '复核所选（1）');
  await h.$('#queue-selected-review').dispatch('click');
  assert.deepEqual(h.scopes[0][0].ids, [1]);
});

test('batch actions only act on selected eligible jobs, not saved records', async () => {
  const h = harness(); h.jobs = [readyJob('ready-1'), readyJob('ready-2'), readyJob('queued-1', {status: 'queued', start_requested: true})];
  await h.controller.loadDailyResults();
  for (const id of ['ready-1', 'queued-1']) { const input = node({workSelect: 'job:' + id}); input.checked = true; await h.$('#queue').dispatch('change', input); }
  assert.equal(h.$('#queue-selected-start').textContent, '开始所选（1）');
  assert.equal(h.$('#queue-selected-withdraw').textContent, '取消所选排队（1）');
  const read = h.api;
  h.api = async (url, options) => url === '/api/jobs/start' ? releaseJobs(h, options.json.ids) : read(url, options);
  await h.$('#queue-selected-start').dispatch('click');
  assert.deepEqual(h.requests.find(r => r.url === '/api/jobs/start').options.json.ids, ['ready-1']);
  assert.equal(h.jobs.find(j => j.id === 'ready-2').status, 'ready');
});

test('global queue controls stay explicit while day progress reports processed counts', async () => {
  const h = harness(); h.control = {paused: true, status: 'pausing', running: 1, queued: 11};
  h.jobs = [{id: 'running', filename: 'running.jpg', status: 'running'}, {id: 'failed', filename: 'failed.jpg', status: 'failed'}];
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  assert.equal(h.$('#queue-control-label').textContent, '所有日期 · 暂停中');
  assert.match(h.$('#queue-pause-notice').textContent, /所有日期.*11 张/);
  assert.match(h.$('#queue').innerHTML, /正在完成当前图片/);
  assert.equal(h.$('#task-summary').textContent, '已处理 2 / 3 张');
  assert.equal(h.$('#workbench-list-title').textContent, '任务队列');
  h.state.progress.queueControl = {paused: false}; h.controller.renderQueueControl();
  assert.equal(h.$('#queue-pause-notice').classList.contains('hidden'), true);
});

const readyJob = (id, extra = {}) => ({id, filename: `${id}.jpg`, status: 'ready', start_requested: false,
  created_at: '2026-09-10T10:00:00', ...extra});
function releaseJobs(h, ids) {
  const selected = new Set(ids);
  h.jobs = h.jobs.map(job => selected.has(job.id) ? {...job, status: 'queued', start_requested: true} : job);
  return {started: ids.length, items: h.jobs.filter(job => selected.has(job.id)), control: h.control};
}

test('uploaded ready receipts stay pending and do not count as processed', async () => {
  const h = harness(); h.data = []; h.jobs = [readyJob('ready', {uses_remote: true})];
  await h.controller.loadDailyResults();
  assert.equal(h.state.progress.workFilter, 'all');
  assert.equal(ReceiptQueue.labels.ready, '待开始');
  assert.deepEqual(ReceiptQueue.counts([{status: 'ready'}]), {total: 1, completed: 0, succeeded: 0, failed: 0,
    cancelled: 0, awaiting_upload: 0, ready: 1, queued: 0, running: 0, pending_review: 0, status: '待开始'});
  assert.equal(ReceiptWorkbench.category({status: 'ready'}), 'processing');
  assert.match(h.$('#task-summary').textContent, /已处理 0 \/ 1 张 · 1 张待开始/);
  assert.match(h.$('#queue').innerHTML, /待开始/);
  assert.equal(h.$('#queue-start').textContent, '开始全部待开始（1）');
  assert.equal(h.$('#queue-start').disabled, false);
  assert.equal(h.$('#queue-start-remote-note').classList.contains('hidden'), false);
  assert.equal(h.requests.some(request => request.options?.method), false);
});

test('polling preserves explicit status filters', async () => {
  const h = harness(); h.jobs = [readyJob('ready')];
  await h.filters[0].dispatch('click');
  await h.controller.loadDailyResults();
  assert.equal(h.state.progress.workFilter, 'completed');
  await h.filters[2].dispatch('click'); await h.controller.loadDailyResults();
  assert.deepEqual(h.state.progress.workFilters, ['completed', 'failed']);
  const later = harness(); later.data = []; await later.controller.loadDailyResults();
  later.jobs = [readyJob('later')]; await later.controller.loadDailyResults();
  assert.equal(later.state.progress.workFilter, 'all');
});

test('start captures all ready jobs in the loaded day beyond visible rows without confirmation or duplicate writes', async () => {
  const h = harness(), pending = deferred(); h.data = [];
  h.jobs = [...Array.from({length: 61}, (_, index) => readyJob(`ready-${index}`, {uses_remote: true})),
    readyJob('other-day', {created_at: '2026-09-09T10:00:00'}), readyJob('running', {status: 'running', start_requested: true}),
    readyJob('uploading', {status: 'awaiting_upload'})];
  h.environment.window.confirm = () => { throw Error('Unexpected confirmation'); };
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  const read = h.api;
  h.api = async (url, options) => url === '/api/jobs/start' ? pending.promise : read(url, options);
  const starting = h.$('#queue-start').dispatch('click');
  assert.equal(h.$('#queue-start').disabled, true);
  await h.$('#queue-start').dispatch('click');
  const writes = h.requests.filter(request => request.options?.method);
  assert.equal(writes.length, 1); assert.equal(writes[0].url, '/api/jobs/start');
  const ids = writes[0].options.json.ids;
  assert.deepEqual(ids, Array.from({length: 61}, (_, index) => `ready-${index}`));
  pending.resolve(releaseJobs(h, ids)); await starting;
  assert.equal(h.$('#queue-start').textContent, '开始全部待开始（0）');
  assert.equal(h.$('#queue-start').disabled, true);
});

test('start is blocked while scanning or uploading and when the displayed day is stale or failed', async () => {
  const h = harness(); h.data = []; h.jobs = [readyJob('ready')]; await h.controller.loadDailyResults();
  for (const busy of [{importScanning: true}, {batchRunning: true}, {uploadingJobs: new Set(['upload'])}]) {
    Object.assign(h.state.imports, busy); h.controller.syncStartRecognition();
    assert.equal(h.$('#queue-start').disabled, true);
    await h.$('#queue-start').dispatch('click');
    Object.assign(h.state.imports, {importScanning: false, batchRunning: false, uploadingJobs: new Set()});
  }
  h.$('#progress-date').value = '2026-09-09'; await h.$('#queue-start').dispatch('click');
  assert.equal(h.$('#queue-start').disabled, true);
  h.$('#progress-date').value = '2026-09-10'; h.api = async () => { throw Error('offline'); };
  await assert.rejects(h.controller.loadDailyResults(), /offline/);
  await h.$('#queue-start').dispatch('click');
  assert.equal(h.requests.some(request => request.options?.method), false);
});

test('a day change during start never adds the newly displayed day to the captured request', async () => {
  const h = harness(), pending = deferred(); h.data = []; h.jobs = [readyJob('old-day')];
  await h.controller.loadDailyResults(); const read = h.api;
  h.api = async (url, options) => url === '/api/jobs/start' ? pending.promise : read(url, options);
  const starting = h.$('#queue-start').dispatch('click');
  h.$('#progress-date').value = '2026-09-09';
  h.jobs = [readyJob('new-day', {created_at: '2026-09-09T10:00:00'})];
  await h.controller.loadDailyResults();
  pending.resolve({started: 1, items: [readyJob('old-day', {status: 'queued', start_requested: true})], control: h.control});
  await starting;
  assert.deepEqual(h.requests.filter(request => request.url === '/api/jobs/start').map(request => request.options.json.ids), [['old-day']]);
  assert.equal(h.state.progress.queueDay, '2026-09-09');
  assert.equal(h.state.progress.queueJobs.get('new-day').status, 'ready');
  assert.equal(h.state.progress.queueJobs.has('old-day'), false);
  assert.equal(h.$('#queue-start').textContent, '开始全部待开始（1）');
});

test('start batches more than 5000 captured IDs and preserves completed batches after a later failure', async () => {
  const h = harness(); h.data = []; h.jobs = Array.from({length: 5001}, (_, index) => readyJob(`ready-${index}`));
  await h.controller.loadDailyResults(); const read = h.api; let calls = 0;
  h.api = async (url, options) => {
    if (url !== '/api/jobs/start') return read(url, options);
    calls++; assert.equal(h.$('#queue-start').disabled, true);
    if (calls === 2) throw Error('connection lost');
    return releaseJobs(h, options.json.ids);
  };
  await h.$('#queue-start').dispatch('click');
  const writes = h.requests.filter(request => request.url === '/api/jobs/start');
  assert.deepEqual(writes.map(request => request.options.json.ids.length), [5000, 1]);
  assert.equal(writes[1].options.json.ids[0], 'ready-5000');
  assert.equal(h.$('#queue-start').textContent, '开始全部待开始（1）');
  assert.match(h.notices.at(-1), /已提交 5000 张回单，剩余 1 张/);
  h.api = async (url, options) => url === '/api/jobs/start' ? releaseJobs(h, options.json.ids) : read(url, options);
  await h.$('#queue-start').dispatch('click');
  assert.deepEqual(h.requests.filter(request => request.url === '/api/jobs/start').at(-1).options.json.ids, ['ready-5000']);
});

test('global resume cannot start held imports and explicit start does not silently resume a paused queue', async () => {
  const h = harness(); h.data = []; h.jobs = [readyJob('ready')]; h.control = {paused: true, queued: 0, running: 0};
  await h.controller.loadDailyResults(); const read = h.api;
  h.api = async (url, options) => {
    if (url === '/api/queue/control' && options?.method) return h.control = {...h.control, paused: options.json.paused};
    if (url === '/api/jobs/start') return releaseJobs(h, options.json.ids);
    return read(url, options);
  };
  await h.$('#queue-toggle').dispatch('click');
  assert.equal(h.jobs[0].status, 'ready');
  assert.equal(h.requests.filter(request => request.options?.method).length, 1);
  assert.equal(h.$('#queue-start').disabled, false);
  h.control = {paused: true, queued: 0, running: 0}; await h.controller.loadDailyResults();
  await h.$('#queue-start').dispatch('click');
  assert.equal(h.control.paused, true);
  assert.match(h.notices.at(-1), /后台队列已暂停/);
  assert.equal(h.requests.filter(request => request.url === '/api/queue/control' && request.options?.method).length, 1);
});

test('reuploading a held remote job has no confirmation and still waits for explicit start', async () => {
  const h = harness(); h.data = []; h.jobs = [readyJob('reupload', {status: 'awaiting_upload', uses_remote: true})];
  h.environment.window.confirm = () => { throw Error('Unexpected remote confirmation for local upload'); };
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  assert.match(h.$('#queue').innerHTML, /补传后点击“开始识别”/);
  await h.$('#queue').dispatch('click', node({jobUpload: 'reupload'}));
  const read = h.api;
  h.api = async (url, options) => {
    if (options?.method) { h.jobs[0].status = 'ready'; return h.jobs[0]; }
    return read(url, options);
  };
  h.files[0].files = [{}]; await h.files[0].dispatch('change');
  assert.equal(h.jobs[0].start_requested, false);
  assert.equal(h.$('#queue-start').disabled, false);
  assert.deepEqual(h.requests.filter(request => request.options?.method).map(request => request.url), ['/api/jobs/reupload/upload']);
});

test('removing a held import asks first and uses the guarded queue endpoint', async () => {
  const h = harness(); h.data = []; h.jobs = [readyJob('remove-1')];
  await h.controller.loadDailyResults();
  await h.$('#queue').dispatch('click', node({workDelete: 'job:remove-1'}));
  assert.equal(h.requests.some(r => r.options?.method), false);
  h.confirm = true;
  const read = h.api;
  h.api = async (url, options) => {
    if (options?.method) { h.jobs = []; return {updated: 1}; }
    return read(url, options);
  };
  await h.$('#queue').dispatch('click', node({workDelete: 'job:remove-1'}));
  assert.deepEqual(h.requests.filter(r => r.options?.method).map(r => [r.url, r.options.json]),
    [['/api/jobs/delete', {ids: ['remove-1']}]]);
  assert.doesNotMatch(h.$('#queue').innerHTML, /remove-1.jpg/);
});

test('queue display sorts imports newest first across statuses and polling updates', async () => {
  const h = harness(); h.data = [];
  h.state.progress.workFilters = ['ready', 'queued', 'running', 'completed', 'failed'];
  h.jobs = [
    readyJob('old-running', {status: 'running', created_at: '2026-09-10T10:00:00'}),
    readyJob('recent-done', {status: 'succeeded', created_at: '2026-09-10T12:00:00', result_id: 1}),
    readyJob('middle-queued', {status: 'queued', priority: 100, created_at: '2026-09-10T11:00:00'})
  ];
  await h.controller.loadDailyResults();
  const order = () => [...h.$('#queue').innerHTML.matchAll(/data-work-select="job:([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(order(), ['recent-done', 'middle-queued', 'old-running']);
  h.jobs.push(readyJob('new-import', {created_at: '2026-09-10T13:00:00'}));
  await h.controller.loadDailyResults({refreshRecords: false});
  assert.deepEqual(order(), ['new-import', 'recent-done', 'middle-queued', 'old-running']);
});

test('completed record can be deleted singly and cancellation makes no write', async () => {
  const h = harness(); h.state.progress.workFilters = ['completed'];
  await h.controller.loadDailyResults();
  assert.match(h.$('#queue').innerHTML, /data-work-delete="record:1"/);
  await h.$('#queue').dispatch('click', node({workDelete: 'record:1'}));
  assert.equal(h.requests.some(r => r.options?.method), false);
  const read = h.api; h.confirm = true;
  h.api = async (url, options) => {
    if (url === '/api/results/delete') { assert.deepEqual(options.json.ids, [1]); h.data = []; return {ids: [1]}; }
    return read(url, options);
  };
  await h.$('#queue').dispatch('click', node({workDelete: 'record:1'}));
  assert.doesNotMatch(h.$('#queue').innerHTML, /receipt-1.jpg/);
  assert.equal(h.state.records.recordsStale, true);
});

test('bulk deletion includes failed and completed rows, but preserves a job claimed meanwhile', async () => {
  const h = harness(); h.confirm = true;
  h.state.progress.workFilters = ['ready', 'queued', 'running', 'completed', 'failed'];
  h.jobs = [readyJob('done', {status: 'succeeded', result_id: 1}), readyJob('failure', {status: 'failed'}),
    readyJob('pending', {status: 'queued', target_result_id: 2}), readyJob('running', {status: 'running'})];
  h.data = [row(1), row(2)];
  await h.controller.loadDailyResults();
  assert.doesNotMatch(h.$('#queue').innerHTML, /data-work-delete="job:running"/);
  const all = h.$('#workbench-select-all'); all.checked = true; await all.dispatch('change');
  assert.equal(h.$('#queue-selected-delete').textContent, '删除所选（3）');
  const read = h.api;
  h.api = async (url, options) => {
    if (url === '/api/jobs/delete') {
      assert.deepEqual(new Set(options.json.ids), new Set(['done', 'failure', 'pending']));
      h.jobs = h.jobs.filter(job => ['pending', 'running'].includes(job.id));
      h.jobs[0].status = 'running';
      return {deleted_ids: ['done', 'failure'], kept_ids: ['pending'], result_ids: [1]};
    }
    if (url === '/api/results/delete') { assert.deepEqual(options.json.ids, [1]); h.data = [row(2)]; return {ids: [1]}; }
    return read(url, options);
  };
  await h.$('#queue-selected-delete').dispatch('click');
  assert.match(h.notices.at(-1), /1 张已开始识别/);
  assert.match(h.$('#queue').innerHTML, /pending.jpg/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /done.jpg|failure.jpg/);
});

test('deletion locks duplicate clicks and refreshes records even after partial failure', async () => {
  const h = harness(); h.confirm = true; h.state.progress.workFilters = ['completed'];
  h.jobs = [readyJob('done', {status: 'succeeded', result_id: 1})];
  await h.controller.loadDailyResults();
  const read = h.api, pending = deferred();
  h.api = async (url, options) => {
    if (url === '/api/jobs/delete') { await pending.promise; h.jobs = []; return {deleted_ids: ['done'], result_ids: [1]}; }
    if (url === '/api/results/delete') throw Error('记录删除失败');
    return read(url, options);
  };
  const click = () => h.$('#queue').dispatch('click', node({workDelete: 'job:done'}));
  const saving = click(); await click();
  assert.equal(h.requests.filter(r => r.url === '/api/jobs/delete').length, 1);
  pending.resolve(); await saving;
  assert.match(h.notices.at(-1), /删除未全部完成/);
  assert.match(h.$('#queue').innerHTML, /data-work-delete="record:1"/);
  assert.equal(h.state.records.recordsStale, true);
});
