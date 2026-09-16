const assert = require('node:assert/strict');
const test = require('node:test');
const {createProgress} = require('../static/modules/progress.mjs');
const {createState} = require('../static/modules/state.mjs');
const {workbenchAttention} = require('../static/modules/progress_presentation.mjs');
const ReceiptWorkbench = require('../static/workbench.js');
const ReceiptQueue = require('../static/queue_state.js');

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
  const filters = ['review', 'processing', 'failed'].map(workFilter => node({workFilter}));
  const counts = ['review', 'processing', 'failed'].map(workCount => node({workCount}));
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
    startReviewScope: async (...args) => { scopes.push(args); return h.openReview?.(); },
  });
  controller.initialize();
  return Object.assign(h, {$, state, filters, counts, requests, notices, scopes, files, environment, controller});
}

test('workbench starts with pending review and excludes resolved and cancelled receipts', async () => {
  const h = harness();
  h.data = [row(1), row(2, {review_status: '已复核', final_result: '通过'}), row(3, {review_status: '已复核', final_result: '不通过'})];
  h.jobs = [{id: 'cancelled', filename: 'cancelled.jpg', status: 'cancelled'}, {id: 'waiting', filename: 'waiting.jpg', status: 'queued'}];
  assert.equal(h.state.progress.workFilter, 'review');
  await h.controller.loadDailyResults();
  assert.match(h.$('#queue').innerHTML, /receipt-1.jpg/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /receipt-2|receipt-3|cancelled.jpg|waiting.jpg/);
  assert.equal((h.$('#queue').innerHTML.match(/<td/g) || []).length, 3);
  assert.equal(h.$('#workbench-total-label').textContent, '当天共 5 张');
  assert.deepEqual(h.counts.map(count => count.textContent), [1, 1, 0]);
  assert.equal(h.$('#progress-note').textContent, '');
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
  h.data = [row(1, {filename: unsafe, fields: {客户名称: unsafe}, date_check: {status: '匹配', reliable: true}, seal_check: {status: '匹配', reliable: true}, review_reasons: [unsafe]})];
  await h.controller.loadDailyResults();
  assert.doesNotMatch(h.$('#queue').innerHTML, /<img|<script/);
  assert.match(h.$('#queue').innerHTML, /&quot;&gt;&lt;img/);
  h.jobs = [{id: unsafe, filename: 'failed.jpg', status: 'failed', error_message: unsafe}];
  await h.controller.loadDailyResults(); await h.filters[2].dispatch('click');
  assert.match(h.$('#queue').innerHTML, /data-job-retry="&quot;&gt;&lt;img/);
  assert.doesNotMatch(h.$('#queue').innerHTML, /<img/);
});

test('expanding the list survives polling with stable receipt order and resets on tabs and dates', async () => {
  const h = harness(); h.data = Array.from({length: 120}, (_, index) => row(index + 1));
  await h.controller.loadDailyResults();
  assert.equal((h.$('#queue').innerHTML.match(/data-work-review=/g) || []).length, 50);
  await h.$('#workbench-more').dispatch('click');
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 100 / 120 张');
  h.data = [row(121), ...h.data.slice().reverse()];
  await h.controller.loadDailyResults();
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 100 / 121 张');
  assert.equal(h.$('#queue').innerHTML.match(/data-work-review="(\d+)"/)[1], '1');
  await h.filters[1].dispatch('click'); await h.filters[0].dispatch('click');
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 50 / 121 张');
  await h.$('#workbench-more').dispatch('click');
  h.$('#progress-date').value = '2026-09-09'; await h.controller.loadDailyResults();
  assert.equal(h.$('#workbench-page-note').textContent, '已显示 50 / 121 张');
});

test('start review uses the first pending receipt in the loaded day and prevents duplicate opens', async () => {
  const h = harness(), pending = deferred();
  h.data = [row(2, {review_status: '已复核', final_result: '通过'}), row(7), row(8)];
  await h.$('#workbench-start-review').dispatch('click'); assert.equal(h.scopes.length, 0);
  await h.controller.loadDailyResults(); h.openReview = () => pending.promise;
  const opening = h.$('#workbench-start-review').dispatch('click');
  await h.$('#workbench-start-review').dispatch('click'); assert.equal(h.scopes.length, 1);
  assert.deepEqual(h.scopes[0], [{kind: 'day', label: '2026-09-10 · 当天全部', filters: {import_date: '2026-09-10'}}, {recordId: 7}]);
  pending.resolve(); await opening;
  assert.equal(h.$('#workbench-start-review').disabled, false);
  await h.$('#queue').dispatch('click', node({workReview: '8'}));
  assert.deepEqual(h.scopes[1][1], {recordId: 8});
  assert.equal(h.scopes[1][0].filters.import_date, '2026-09-10');
});

test('date changes hide old rows and clear old progress; failed requests cannot start stale review', async () => {
  const h = harness(); await h.controller.loadDailyResults();
  const before = h.$('#queue').innerHTML;
  h.api = async () => { throw Error('offline'); };
  await assert.rejects(h.controller.loadDailyResults(), /offline/);
  assert.equal(h.$('#queue').innerHTML, before);
  assert.equal(h.$('#workbench-start-review').disabled, true);
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

test('cancel deletes an unfinished queue item after confirmation', async () => {
  const h = harness();
  h.jobs = [{id: 'cancel/1', filename: 'cancel.jpg', status: 'ready', start_requested: false}];
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  assert.match(h.$('#queue').innerHTML, /data-job-cancel="cancel\/1"/);
  const button = node({jobCancel: 'cancel/1'});
  await h.$('#queue').dispatch('click', button);
  assert.equal(h.requests.some(request => request.options?.method), false);

  h.confirm = true;
  const read = h.api;
  h.api = async (url, options) => {
    if (options?.method) { h.jobs = []; return {deleted: true}; }
    return read(url, options);
  };
  await h.$('#queue').dispatch('click', button);
  assert.equal(h.requests.find(request => request.options?.method)?.url, '/api/jobs/cancel%2F1/cancel');
  assert.doesNotMatch(h.$('#queue').innerHTML, /cancel\.jpg/);
  assert.equal(h.notices.at(-1), '已取消并删除这条待处理回单。');
});

test('cancel all deletes every unfinished item in the current day', async () => {
  const h = harness();
  h.jobs = [
    {id: 'ready-1', filename: 'one.jpg', status: 'ready', start_requested: false},
    {id: 'running-1', filename: 'two.jpg', status: 'running'},
    {id: 'done-1', filename: 'done.jpg', status: 'succeeded'}
  ];
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  assert.equal(h.$('#workbench-cancel-all').textContent, '全部取消（2）');
  h.confirm = true;
  const read = h.api;
  h.api = async (url, options) => {
    if (options?.method) { h.jobs = [{id: 'done-1', filename: 'done.jpg', status: 'succeeded'}]; return {deleted_ids: ['ready-1', 'running-1']}; }
    return read(url, options);
  };
  await h.$('#workbench-cancel-all').dispatch('click');
  assert.equal(h.requests.find(request => request.options?.method)?.url, '/api/jobs/cancel');
  assert.match(h.requests.find(request => request.options?.method)?.options.json.ids.join(','), /ready-1/);
  assert.match(h.notices.at(-1), /删除 2 张/);
});

test('global queue controls stay explicit while day progress reports processed counts', async () => {
  const h = harness(); h.control = {paused: true, status: 'pausing', running: 1, queued: 11};
  h.jobs = [{id: 'running', filename: 'running.jpg', status: 'running'}, {id: 'failed', filename: 'failed.jpg', status: 'failed'}];
  await h.controller.loadDailyResults(); await h.filters[1].dispatch('click');
  assert.equal(h.$('#queue-control-label').textContent, '所有日期 · 暂停中');
  assert.match(h.$('#queue-pause-notice').textContent, /所有日期.*11 张/);
  assert.match(h.$('#queue').innerHTML, /正在完成当前图片/);
  assert.equal(h.$('#task-summary').textContent, '已处理 2 / 3 张');
  assert.equal(h.$('#workbench-reason-heading').textContent, '处理进度');
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
  assert.equal(h.state.progress.workFilter, 'processing');
  assert.equal(h.filters[1].attributes['aria-pressed'], 'true');
  assert.equal(ReceiptQueue.labels.ready, '待开始');
  assert.deepEqual(ReceiptQueue.counts([{status: 'ready'}]), {total: 1, completed: 0, succeeded: 0, failed: 0,
    cancelled: 0, awaiting_upload: 0, ready: 1, queued: 0, running: 0, pending_review: 0, status: '待开始'});
  assert.equal(ReceiptWorkbench.category({status: 'ready'}), 'processing');
  assert.match(h.$('#task-summary').textContent, /已处理 0 \/ 1 张 · 1 张待开始/);
  assert.match(h.$('#queue').innerHTML, /待开始/);
  assert.equal(h.$('#queue-start').textContent, '开始识别（1）');
  assert.equal(h.$('#queue-start').disabled, false);
  assert.equal(h.$('#queue-start-remote-note').classList.contains('hidden'), false);
  assert.equal(h.requests.some(request => request.options?.method), false);
});

test('initial ready-list fallback preserves explicit filters and never switches a later polling result', async () => {
  const h = harness(); h.data = []; h.jobs = [readyJob('ready')];
  await h.filters[0].dispatch('click');
  await h.controller.loadDailyResults();
  assert.equal(h.state.progress.workFilter, 'review');
  await h.filters[2].dispatch('click'); await h.controller.loadDailyResults();
  assert.equal(h.state.progress.workFilter, 'failed');
  const later = harness(); later.data = []; await later.controller.loadDailyResults();
  later.jobs = [readyJob('later')]; await later.controller.loadDailyResults();
  assert.equal(later.state.progress.workFilter, 'review');
  const mixed = harness(); mixed.jobs = [readyJob('with-review')]; await mixed.controller.loadDailyResults();
  assert.equal(mixed.state.progress.workFilter, 'review');
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
  assert.equal(h.$('#queue-start').textContent, '开始识别（0）');
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
  assert.equal(h.$('#queue-start').textContent, '开始识别（1）');
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
  assert.equal(h.$('#queue-start').textContent, '开始识别（1）');
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
