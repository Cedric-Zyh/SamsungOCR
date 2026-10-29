const assert = require('node:assert/strict');
const test = require('node:test');
const {createRecords} = require('../static/modules/records/records.mjs');
const {createImports} = require('../static/modules/imports/imports.mjs');
const {createState} = require('../static/modules/core/state.mjs');
const ReceiptWorkbench = require('../static/modules/core/workbench.mjs').ReceiptWorkbench;

function node() {
  const events = new Map(), classes = new Set();
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, checked: false, attributes: {}, dataset: {}, style: {},
    classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name), toggle: (name, yes) => yes ? classes.add(name) : classes.delete(name)},
    setAttribute(name, value) { this.attributes[name] = String(value); }, removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(name, callback) { if (!events.has(name)) events.set(name, []); events.get(name).push(callback); },
    async dispatch(event) {
      for (const callback of events.get(event.type) || []) await callback(event);
      if (!event.propagationStopped && this.parentNode) await this.parentNode.dispatch(event);
    },
    async emit(name, target = this) {
      const event = {type: name, target, propagationStopped: false, stopPropagation() { this.propagationStopped = true; }};
      await this.dispatch(event);
    },
    focus() {}, click() {},
  };
}

function harness({storedSize = null} = {}) {
  const nodes = new Map(), collections = new Map(), fields = {}, requests = [], notices = [], timers = [], writes = [], scrolls = [], reviewScopes = [];
  const $ = selector => {
    const name = selector === '#import-date' ? 'import_date' : selector.match(/^#filters \[name=["']?([^"'\]]+)["']?\]$/)?.[1];
    if (name) return fields[name] || (fields[name] = node());
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  };
  for (const key of ['import_date', 'task_id', 'text_match', 'search', 'customer', 'overall', 'date_status', 'seal_status', 'review_status']) fields[key] = node();
  fields.text_match.value = 'prefix';
  $('#filters').elements = {namedItem: key => fields[key]};
  const recordsState = createState().records;
  let respond = async () => ({items: [], total: 0, page: 1, page_size: recordsState.recordPageSize});
  let refreshed = 0;
  const environment = {
    document: {...node(), activeElement: null}, window: {...node(), innerHeight: 900, matchMedia: () => ({matches: true})},
    location: {hash: '#records'}, URLSearchParams, AbortController,
    FormData: class { [Symbol.iterator]() { return Object.entries(fields).map(([key, field]) => [key, field.value])[Symbol.iterator](); } },
    setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {},
    localStorage: {getItem: key => key === 'receipt.records.page-size' ? storedSize : null, setItem: (key, value) => writes.push([key, value])},
  };
  $('.record-filter-table').scrollTo = options => scrolls.push(options);
  $('.record-filter-table').getBoundingClientRect = () => ({top: 250});
  const controller = createRecords({environment, ui: {$, $$: selector => collections.get(selector) || [], toast: message => notices.push(message)}, recordsState, ReceiptWorkbench,
    api: async (url, options) => { requests.push({url, options}); return respond(url, options); }, startReviewScope: async (...args) => { reviewScopes.push(args); }, retryOne: async () => {},
    refreshVisibleResults: async () => { refreshed++; }, setBatchStep() {},
  });
  controller.initialize();
  return {controller, recordsState, $, fields, requests, notices, timers, writes, scrolls, environment, collections, reviewScopes,
    respond: callback => { respond = callback; }, get refreshed() { return refreshed; }};
}

const row = (id, ready = false) => ({id, filename: `${id}.jpg`, fields: {}, date_check: {actual: '2026-09-10', status: '匹配', reliable: ready}, seal_check: {recognized: '客户章', status: '匹配', reliable: ready}, review_status: '待复核'});
const page = (items, total = items.length, number = 1, size = 100) => ({items, total, page: number, page_size: size});
const target = name => ({closest: selector => selector === `[${name}]` ? {} : null});
const processingRow = (id, status, resultId = null) => ({
  id: resultId, filename: `${id}.jpg`, fields: {}, processing_status: status,
  processing_job: {id, status, result_id: resultId},
});

function bulkRetryHarness(selected, {remote = true} = {}) {
  const nodes = new Map(), requests = [], notices = [], shownTasks = [];
  let confirmations = 0, settingsFlushes = 0;
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  };
  const task = {id: 'retry-task', items: []};
  const controller = createImports({
    environment: {document: {}, window: {confirm() { confirmations++; return true; }}, location: {}, localStorage: {}},
    ui: {$, $$: () => [], toast: message => notices.push(message)},
    importsState: {ocrBackend: 'vision', sealRecognitionMode: 'local'}, recordsState: {selected: new Set(selected)},
    ReceiptImport: {}, readRecognitionPlan: () => ({fields: ['vision']}), usesRemotePlan: () => remote,
    updateRecognitionPlan() {}, loadDailyResults: async () => {}, closeModal() {},
    flushSettings: async () => { settingsFlushes++; }, showQueuedRetry: async value => { shownTasks.push(value); },
    api: async (url, options) => { requests.push({url, options}); return task; },
  });
  return {controller, $, requests, notices, shownTasks, task,
    get confirmations() { return confirmations; }, get settingsFlushes() { return settingsFlushes; }};
}

test('empty records disable review and hide pagination until records return', async () => {
  const h = harness();
  h.fields.import_date.value = '2026-10-13';
  await h.controller.loadRecords();
  assert.equal(h.$('#review-filtered-records').disabled, true);
  assert.equal(h.$('#records-page-prev').classList.contains('hidden'), true);
  assert.equal(h.$('#records-page-info').textContent, '2026年10月13日 · 共 0 张回单');
  assert.match(h.$('#records-body').innerHTML, /当天还没有导入回单/);
  assert.match(h.$('#records-body').innerHTML, /href="#import"/);
  h.respond(async () => page([row(1)], 30, 1, 25));
  await h.controller.loadRecords();
  assert.equal(h.$('#review-filtered-records').disabled, false);
  assert.equal(h.$('#records-page-next').classList.contains('hidden'), false);
  assert.equal(h.$('#records-page-next').disabled, false);
});

test('a failed foreground query clears stale counts and offers an escaped in-place retry', async () => {
  const h = harness();
  h.respond(async () => page([row(1)], 300)); await h.controller.loadRecords();
  h.respond(async () => { throw Error('<script>offline</script>'); });
  await h.controller.loadRecords({page: 2});
  assert.equal(h.$('#record-count').textContent, '数量未更新');
  assert.equal(h.$('#records-page-prev').disabled, true); assert.equal(h.$('#records-page-next').disabled, true);
  assert.equal(h.$('#records-page-size').disabled, false);
  assert.match(h.$('#records-body').innerHTML, /data-record-retry/);
  assert.match(h.$('#records-body').innerHTML, /&lt;script&gt;offline&lt;\/script&gt;/);
  assert.equal(h.recordsState.recordsLoaded, false);
  h.respond(async () => page([row(7)]));
  await h.$('#records-body').emit('click', target('data-record-retry'));
  assert.equal(h.recordsState.records[0].id, 7);
  assert.equal(h.$('#record-count').textContent, '共 1 张');
});

test('empty-result recovery removes additional filters while retaining date and task scope', async () => {
  const h = harness();
  h.fields.import_date.value = '2026-08-20'; h.recordsState.batchFilter = 'task-17';
  h.fields.search.value = '不存在的客户'; h.fields.date_status.value = '不匹配';
  await h.controller.loadRecords();
  assert.match(h.$('#records-body').innerHTML, /data-clear-record-filters/);
  await h.$('#records-body').emit('click', target('data-clear-record-filters'));
  assert.equal(h.fields.search.value, ''); assert.equal(h.fields.date_status.value, '');
  assert.equal(h.fields.import_date.value, '2026-08-20'); assert.equal(h.fields.task_id.value, 'task-17');
  assert.equal(h.fields.text_match.value, 'prefix');
  await h.timers.at(-1)();
  const params = new URL(h.requests.at(-1).url, 'http://local').searchParams;
  assert.equal(params.get('import_date'), '2026-08-20'); assert.equal(params.get('task_id'), 'task-17');
  assert.match(h.$('#records-body').innerHTML, /data-record-date/);
});

test('empty-date action opens the calendar without its original click closing it again', async () => {
  const h = harness();
  await h.controller.loadRecords();
  assert.match(h.$('#records-body').innerHTML, /data-record-date/);
  h.$('#records-body').parentNode = h.environment.document;
  h.$('#calendar-toggle').parentNode = h.environment.document;
  h.$('#import-calendar').classList.add('hidden');
  let toggled;
  // The nested programmatic click comes from inside the calendar wrapper.
  h.$('#calendar-toggle').click = () => {
    toggled = h.$('#calendar-toggle').emit('click', {closest: selector => selector === '.calendar-wrap' ? {} : null});
  };
  await h.$('#records-body').emit('click', target('data-record-date'));
  await toggled;
  assert.equal(h.$('#import-calendar').classList.contains('hidden'), false);
  assert.equal(h.$('#calendar-toggle').attributes['aria-expanded'], 'true');
  await h.environment.document.emit('click', {closest: () => null});
  assert.equal(h.$('#import-calendar').classList.contains('hidden'), true);
});

test('page size accepts only supported values, persists, and clears current-page selection', async () => {
  const h = harness({storedSize: '25'});
  assert.equal(h.recordsState.recordPageSize, 25); assert.equal(h.$('#records-page-size').value, '25');
  h.respond(async url => { const query = new URL(url, 'http://local').searchParams; return page([row(1)], 250, Number(query.get('page')), Number(query.get('page_size'))); });
  await h.controller.loadRecords(); h.controller.toggleSelected(1, true);
  const before = h.requests.length;
  await h.controller.setPageSize('999');
  assert.equal(h.requests.length, before); assert.equal(h.recordsState.recordPageSize, 25);
  await h.controller.setPageSize('50');
  assert.equal(h.recordsState.recordPageSize, 50); assert.equal(h.recordsState.recordPage, 1); assert.equal(h.recordsState.selected.size, 0);
  assert(h.writes.some(([key, value]) => key === 'receipt.records.page-size' && value === '50'));
  assert.equal(h.scrolls.length, 1);
  await h.controller.loadRecords({background: true}); assert.equal(h.scrolls.length, 1);
  await h.$('#records-page-next').emit('click'); assert.equal(h.recordsState.recordPage, 2); assert.equal(h.scrolls.length, 2);
  assert.equal(harness({storedSize: '999'}).recordsState.recordPageSize, 100);
});

test('selection explains the blocking evidence before submission and cancels the current page', async () => {
  const h = harness(); h.respond(async () => page([row(1, true), row(2, false)])); await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  assert.equal(h.$('#selection-count').textContent, '本页已选 2 张');
  assert.equal(h.$('#bulk-pass').disabled, true);
  assert.match(h.$('#selection-readiness').textContent, /1 张需逐张确认/);
  const before = h.requests.length;
  await h.controller.bulkReview('确认通过', '通过');
  assert.equal(h.requests.length, before); assert.match(h.notices.at(-1), /不能批量确认通过/);
  h.controller.toggleSelected(2, false);
  assert.equal(h.$('#bulk-pass').disabled, false); assert.equal(h.$('#selection-readiness').textContent, '选中回单均可批量通过');
  await h.$('#clear-record-selection').emit('click');
  assert.equal(h.recordsState.selected.size, 0); assert.equal(h.$('#select-all').checked, false);
  assert.equal(h.$('#selection-toolbar').classList.contains('hidden'), true);
});

test('batch updates show progress, reject duplicate submissions, and recover controls on failure', async () => {
  const h = harness(); h.respond(async () => page([row(1, true)])); await h.controller.loadRecords(); h.controller.toggleSelected(1, true);
  let reject; h.respond(() => new Promise((_resolve, fail) => { reject = fail; }));
  const before = h.requests.length, pending = h.controller.bulkReview('确认通过', '通过');
  assert.equal(h.$('#bulk-pass').disabled, true); assert.equal(h.$('#bulk-delete').disabled, true);
  assert.equal(h.$('#selection-toolbar').attributes['aria-busy'], 'true');
  assert.equal(h.$('#selection-readiness').textContent, '正在处理选中回单…');
  await h.controller.bulkReview('确认通过', '通过'); await h.controller.deleteRecords([1]);
  assert.equal(h.requests.length, before + 1);
  reject(Error('连接中断')); await pending;
  assert.equal(h.$('#bulk-pass').disabled, false); assert.equal(h.$('#selection-toolbar').attributes['aria-busy'], 'false');
  assert.deepEqual([...h.recordsState.selected], [1]); assert.equal(h.refreshed, 0);
});

test('record rows use internal order IDs, escape metadata, and consolidate matching review decisions', () => {
  const h = harness();
  const item = {...row(1), internal_fields: {'客户订单号': '<ORDER-7>'}, overall: '需人工复核', final_result: '通过', review_status: '确认通过'};
  const markup = h.controller.recordRow(item);
  assert.match(markup, /<strong>&lt;ORDER-7&gt;<\/strong>/);
  assert.doesNotMatch(markup, /title="1.jpg"|<strong>1.jpg<\/strong>|record-file-meta/);
  assert.match(h.controller.recordRow(row(1)), /<strong>1.jpg<\/strong>/);
  assert.match(h.controller.recordRow({...item, fields: {'客户订单号': '73623001'}}), /<strong>73623001<\/strong>/);
  assert.match(markup, /class="record-file-link" data-open-review="1"/);
  assert.match(markup, /class="pill success">已通过<\/span>/);
  assert.doesNotMatch(markup, /<small>确认通过<\/small>/);
  assert.match(markup, /title="通过 · 确认通过"/);
  const needsReview = h.controller.recordRow({...item, final_result: '通过', review_status: '待复核'});
  assert.match(needsReview, /<small>待复核<\/small>/);
  const failed = h.controller.recordRow({...item, final_result: '识别失败', review_status: '待复核'});
  assert.match(failed, /class="pill danger">识别失败<\/span><small>待复核<\/small>/);
  const dateMatch = h.controller.recordRow({...row(1, true), fields: {'要求到货': '2026-09-10'}});
  assert.match(dateMatch, /class="date-actual"><span><em>签收<\/em>2026-09-10<\/span><span class="check-status success" title="匹配">匹配<\/span><\/small>/);
  const dateMismatch = h.controller.recordRow({...row(1, true), date_check: {actual: '2026-09-09', status: '不匹配', reliable: true}});
  assert.match(dateMismatch, /class="date-actual date-difference"><span><em>签收<\/em>2026-09-09<\/span><span class="check-status danger" title="不匹配">不一致<\/span><\/small>/);
  assert.match(dateMatch, /<td><span class="check-status success" title="匹配">匹配<\/span><\/td>/);
});

test('background row refresh preserves table scroll position', async () => {
  const h = harness(); h.respond(async () => page([row(1), row(2)]));
  await h.controller.loadRecords();
  h.$('.record-filter-table').scrollTop = 540;
  h.$('.record-filter-table').scrollLeft = 32;
  let html = h.$('#records-body').innerHTML;
  Object.defineProperty(h.$('#records-body'), 'innerHTML', {get: () => html, set(value) {
    html = value; h.$('.record-filter-table').scrollTop = h.$('.record-filter-table').scrollLeft = 0;
  }});
  await h.controller.loadRecords({background: true});
  assert.equal(h.$('.record-filter-table').scrollTop, 540);
  assert.equal(h.$('.record-filter-table').scrollLeft, 32);
});

test('inactive processing rows can be selected and deleted while running rows stay protected', () => {
  const h = harness();
  for (const status of ['awaiting_upload', 'ready', 'queued', 'failed']) {
    const markup = h.controller.recordRow(processingRow(`task-${status}`, status));
    const checkbox = markup.match(/<input\b[^>]*>/)?.[0];
    assert(checkbox, `${status} row has a checkbox`);
    assert.match(checkbox, /class="record-select"/);
    assert.match(checkbox, new RegExp(`value="job:task-${status}"`));
    assert.doesNotMatch(checkbox, /\bdisabled\b/);
    assert.match(markup, new RegExp(`data-delete-job="task-${status}"`));
  }
  const running = h.controller.recordRow(processingRow('task-running', 'running', 81));
  assert.match(running.match(/<input\b[^>]*>/)[0], /\bdisabled\b/);
  assert.doesNotMatch(running, /data-delete-job=/);
});

test('select current page includes unuploaded jobs and ordinary results without enabling mixed review', async () => {
  const h = harness();
  h.respond(async () => page([
    row(1, true), processingRow('upload-1', 'awaiting_upload'), processingRow('ready-1', 'ready'),
    processingRow('queued-1', 'queued'), processingRow('failed-1', 'failed', 8), processingRow('running-1', 'running', 9),
  ]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  assert.deepEqual(new Set(h.recordsState.selected), new Set([1, 'job:upload-1', 'job:ready-1', 'job:queued-1', 'job:failed-1']));
  assert.equal(h.$('#select-all').checked, true);
  assert.equal(h.$('#bulk-delete').disabled, false);
  for (const selector of ['#bulk-pass', '#bulk-pending', '#bulk-retry', '#review-selected-records']) {
    assert.equal(h.$(selector).disabled, true, `${selector} blocks mixed processing selection`);
  }
  const before = h.requests.length;
  await h.controller.bulkReview('确认通过', '通过');
  await h.controller.bulkReview('待复核', '需人工复核');
  await h.controller.startRecordsReview('selected');
  assert.equal(h.requests.length, before);
  assert.equal(h.reviewScopes.length, 0);
  h.controller.selectCurrentPage(false);
  assert.equal(h.recordsState.selected.size, 0);
  assert.equal(h.$('#select-all').checked, false);
});

test('processing checkbox events retain the job key instead of converting it into a number', async () => {
  const h = harness(), checkbox = node();
  checkbox.value = 'job:upload-1'; checkbox.checked = true;
  h.collections.set('.record-select', [checkbox]);
  h.respond(async () => page([processingRow('upload-1', 'awaiting_upload')]));
  await h.controller.loadRecords();
  await checkbox.emit('change');
  assert.deepEqual([...h.recordsState.selected], ['job:upload-1']);
  assert.equal(h.$('#bulk-delete').disabled, false);
  checkbox.checked = false;
  await checkbox.emit('change');
  assert.equal(h.recordsState.selected.size, 0);
});

test('mixed deletion removes queue jobs first and then all distinct related result IDs', async () => {
  const h = harness();
  h.respond(async () => page([row(11, true), processingRow('failed-1', 'failed', 12), processingRow('ready-1', 'ready')]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  const before = h.requests.length;
  h.respond(async url => {
    if (url === '/api/jobs/delete') return {deleted: ['failed-1', 'ready-1'], result_ids: [12, 11, 12]};
    if (url === '/api/results/delete') return {deleted: 2};
    throw Error(`Unexpected request: ${url}`);
  });
  await h.controller.deleteRecords([...h.recordsState.selected]);
  const calls = h.requests.slice(before);
  assert.deepEqual(calls.map(call => call.url), ['/api/jobs/delete', '/api/results/delete']);
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(new Set(calls[0].options.json.ids), new Set(['failed-1', 'ready-1']));
  assert.deepEqual(new Set(calls[1].options.json.ids), new Set([11, 12]));
  assert.equal(calls[1].options.json.ids.length, 2);
  assert(calls[1].options.json.ids.every(Number.isSafeInteger));
  assert.equal(h.refreshed, 1);
});

test('deleting only unuploaded jobs never sends empty or job IDs to result deletion', async () => {
  const h = harness();
  h.respond(async () => page([processingRow('upload-1', 'awaiting_upload')]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  const before = h.requests.length;
  h.respond(async url => {
    assert.equal(url, '/api/jobs/delete');
    return {deleted: ['upload-1'], result_ids: []};
  });
  await h.controller.deleteRecords([...h.recordsState.selected]);
  const calls = h.requests.slice(before);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].options.json.ids, ['upload-1']);
  assert.equal(h.refreshed, 1);
});

test('queue deletion failure preserves selection and never deletes the ordinary result first', async () => {
  const h = harness();
  h.respond(async () => page([row(11, true), processingRow('queued-1', 'queued')]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  const before = h.requests.length;
  h.respond(async () => { throw Error('任务状态已更新'); });
  await h.controller.deleteRecords([...h.recordsState.selected]);
  assert.deepEqual(h.requests.slice(before).map(call => call.url), ['/api/jobs/delete']);
  assert.deepEqual(new Set(h.recordsState.selected), new Set([11, 'job:queued-1']));
  assert.equal(h.$('#bulk-delete').disabled, false);
  assert.equal(h.refreshed, 0);
});

test('a queued job that starts running during deletion is kept without removing its previous result', async () => {
  const h = harness();
  h.respond(async () => page([processingRow('q', 'queued', 12)]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  const before = h.requests.length;
  h.respond(async url => {
    assert.equal(url, '/api/jobs/delete');
    return {deleted: true, deleted_ids: [], kept_ids: ['q'], result_ids: []};
  });
  await h.controller.deleteRecords([...h.recordsState.selected]);
  assert.deepEqual(h.requests.slice(before).map(call => call.url), ['/api/jobs/delete']);
  assert.deepEqual(h.requests.at(-1).options.json.ids, ['q']);
  assert(h.notices.some(message => /保留/.test(message)));
  assert.equal(h.refreshed, 1);
});

test('successful queue deletion still refreshes rows if deleting its related result fails', async () => {
  const h = harness();
  h.respond(async () => page([row(11, true), processingRow('failed-1', 'failed', 12)]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  const before = h.requests.length;
  h.respond(async url => {
    if (url === '/api/jobs/delete') return {deleted: true, deleted_ids: ['failed-1'], kept_ids: [], result_ids: [12]};
    assert.equal(url, '/api/results/delete');
    throw Error('回单记录删除失败');
  });
  await h.controller.deleteRecords([...h.recordsState.selected]);
  assert.deepEqual(h.requests.slice(before).map(call => call.url), ['/api/jobs/delete', '/api/results/delete']);
  assert.equal(h.refreshed, 1);
  assert(h.notices.some(message => /回单记录删除失败/.test(message)));
  assert.equal(h.$('#selection-toolbar').attributes['aria-busy'], 'false');
});

test('background polling keeps selectable job keys and clears vanished or newly running selections', async () => {
  const h = harness();
  h.respond(async () => page([
    row(1, true), processingRow('stays-ready', 'ready'), processingRow('starts-running', 'queued'),
    processingRow('vanishes', 'awaiting_upload'),
  ]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  h.respond(async () => page([row(1, true), processingRow('stays-ready', 'queued'), processingRow('starts-running', 'running')]));
  await h.controller.loadRecords({background: true});
  assert.deepEqual(new Set(h.recordsState.selected), new Set([1, 'job:stays-ready']));
  assert.equal(h.$('#bulk-delete').disabled, false);
  assert.equal(h.$('#select-all').checked, true);
  assert.match(h.controller.recordRow(h.recordsState.records[1]), /\bchecked\b/);
  h.respond(async () => page([row(1, true)]));
  await h.controller.loadRecords({background: true});
  assert.deepEqual([...h.recordsState.selected], [1]);
  assert.equal(h.$('#bulk-pass').disabled, false);
  assert.equal(h.$('#bulk-pending').disabled, false);
  assert.equal(h.$('#bulk-retry').disabled, false);
  assert.equal(h.$('#review-selected-records').disabled, false);
});

test('single processing row delete button uses queue deletion without selecting its checkbox', async () => {
  const h = harness(), button = node();
  button.dataset.deleteJob = 'upload-1';
  button.closest = selector => selector === '[data-delete-job]' ? button : null;
  button.parentNode = h.$('#records-body');
  h.collections.set('[data-delete-job]', [button]);
  h.respond(async () => page([processingRow('upload-1', 'awaiting_upload')]));
  await h.controller.loadRecords();
  const before = h.requests.length;
  h.respond(async () => ({deleted: ['upload-1'], result_ids: []}));
  await button.emit('click');
  assert.deepEqual(h.requests.slice(before).map(call => call.url), ['/api/jobs/delete']);
  assert.deepEqual(h.requests.at(-1).options.json.ids, ['upload-1']);
  assert.equal(h.refreshed, 1);
});

test('ordinary result selection continues to support bulk review and result-only deletion', async () => {
  const h = harness();
  h.respond(async () => page([row(11, true)]));
  await h.controller.loadRecords();
  h.controller.selectCurrentPage(true);
  assert.equal(h.$('#bulk-pass').disabled, false);
  assert.equal(h.$('#bulk-pending').disabled, false);
  assert.equal(h.$('#bulk-retry').disabled, false);
  assert.equal(h.$('#review-selected-records').disabled, false);
  const before = h.requests.length;
  h.respond(async () => ({updated: 1}));
  await h.controller.bulkReview('确认通过', '通过');
  assert.equal(h.requests.at(-1).url, '/api/results/bulk-review');
  assert.deepEqual(h.requests.at(-1).options.json.ids, [11]);
  await h.controller.startRecordsReview('selected');
  assert.deepEqual(h.reviewScopes[0][0].ids, [11]);
  await h.controller.deleteRecords([11]);
  assert.equal(h.requests.at(-1).url, '/api/results/delete');
  assert.deepEqual(h.requests.at(-1).options.json.ids, [11]);
  assert.equal(h.requests.length, before + 2);
});

for (const selected of [['job:q'], [11, 'job:q'], ['11']]) {
  test(`bulk retry rejects non-result selection ${JSON.stringify(selected)} before remote confirmation`, async () => {
    const h = bulkRetryHarness(selected);
    await h.controller.bulkRetry();
    assert.equal(h.confirmations, 0);
    assert.equal(h.settingsFlushes, 0);
    assert.deepEqual(h.requests, []);
    assert.deepEqual(h.shownTasks, []);
  });
}

test('bulk retry of ordinary numeric result IDs retains confirmation and retry behavior', async () => {
  const h = bulkRetryHarness([11, 12]);
  await h.controller.bulkRetry();
  assert.equal(h.confirmations, 1);
  assert.equal(h.settingsFlushes, 1);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/results/bulk-retry');
  assert.equal(h.requests[0].options.method, 'POST');
  assert.deepEqual(h.requests[0].options.json.ids, [11, 12]);
  assert.deepEqual(h.shownTasks, [h.task]);
  assert.equal(h.$('#bulk-retry').disabled, false);
});
