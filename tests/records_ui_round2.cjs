const assert = require('node:assert/strict');
const test = require('node:test');
const {createRecords} = require('../static/modules/records.mjs');
const {createState} = require('../static/modules/state.mjs');
const ReceiptWorkbench = require('../static/workbench.js');

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
  const nodes = new Map(), fields = {}, requests = [], notices = [], timers = [], writes = [], scrolls = [];
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
  const controller = createRecords({environment, ui: {$, $$: () => [], toast: message => notices.push(message)}, recordsState, ReceiptWorkbench,
    api: async (url, options) => { requests.push({url, options}); return respond(url, options); }, startReviewScope: async () => {}, retryOne: async () => {},
    refreshVisibleResults: async () => { refreshed++; }, setBatchStep() {},
  });
  controller.initialize();
  return {controller, recordsState, $, fields, requests, notices, timers, writes, scrolls, environment,
    respond: callback => { respond = callback; }, get refreshed() { return refreshed; }};
}

const row = (id, ready = false) => ({id, filename: `${id}.jpg`, fields: {}, date_check: {actual: '2026-09-10', status: '匹配', reliable: ready}, seal_check: {recognized: '客户章', status: '匹配', reliable: ready}, review_status: '待复核'});
const page = (items, total = items.length, number = 1, size = 100) => ({items, total, page: number, page_size: size});
const target = name => ({closest: selector => selector === `[${name}]` ? {} : null});

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
  assert.match(markup, /订单 &lt;ORDER-7&gt;/);
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
