const assert = require('node:assert/strict');
const test = require('node:test');
const {createRecords} = require('../static/modules/records.mjs');
const {createState} = require('../static/modules/state.mjs');
const ReceiptWorkbench = require('../static/workbench.js');

function element() {
  const classes = new Set(), listeners = new Map();
  return {
    value: '', defaultValue: '', textContent: '', innerHTML: '', dataset: {}, attributes: {}, style: {},
    disabled: false, checked: false,
    classList: {
      add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value),
      toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(name, listener) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(listener);
    },
    async emit(name, target = this) {
      for (const listener of listeners.get(name) || []) await listener({target, preventDefault() {}, stopPropagation() {}});
    },
    focus() {},
  };
}

function harness() {
  const nodes = new Map(), fields = {}, timers = new Map(), requests = [], notices = [];
  const tabs = ['', '需人工复核', '通过'].map(value => Object.assign(element(), {dataset: {filter: value}}));
  for (const key of ['import_date', 'task_id', 'text_match', 'search', 'search_prefix', 'customer', 'date', 'date_status', 'seal_status', 'overall', 'review_status']) fields[key] = element();
  const $ = selector => {
    const key = selector === '#import-date' ? 'import_date' : selector.match(/^#filters \[name=["']?([^"'\]]+)["']?\]$/)?.[1];
    if (key) return fields[key];
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  $('#filters').elements = {namedItem: key => fields[key]};
  const recordsState = createState().records;
  let timerId = 0, respond = async () => ({items: [], total: 0, page: 1, page_size: recordsState.recordPageSize});
  const environment = {
    document: {...element(), activeElement: null}, window: element(), location: {hash: '#progress'},
    URLSearchParams, AbortController,
    FormData: class {
      constructor() { this.entries = Object.entries(fields).map(([key, field]) => [key, field.value]); }
      [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
    },
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    localStorage: {getItem: () => null, setItem() {}},
  };
  const controller = createRecords({environment,
    ui: {$, $$: selector => selector === '[data-filter]' ? tabs : [], toast: message => notices.push(message)},
    recordsState, ReceiptWorkbench,
    api: async (url, options) => { requests.push({url, options}); return respond(url, options); },
    startReviewScope: async () => {}, retryOne: async () => {}, refreshVisibleResults: async () => {}, setBatchStep() {},
  });
  controller.initialize();
  return {controller, recordsState, environment, $, fields, tabs, timers, requests, notices,
    respond: callback => { respond = callback; },
    async flushTimers() { for (const [id, callback] of [...timers]) { if (timers.delete(id)) await callback(); } },
  };
}

function applyOldScope(h) {
  for (const field of Object.values(h.fields)) field.value = '旧筛选';
  h.fields.import_date.value = '2026-08-11';
  Object.assign(h.recordsState, {
    batchFilter: 'old-batch', records: [{id: 91, filename: '旧回单.jpg'}], recordPage: 4,
    recordTotal: 200, recordsLoaded: true, recordFilterKey: 'old-scope', recordPageSize: 25, compact: true,
  });
  h.recordsState.selected.add(91);
  h.$('#batch-scope').classList.remove('hidden');
  h.$('#records-body').innerHTML = '旧回单';
  h.$('#records-page-info').textContent = '第 4 / 8 页';
  h.$('#record-count').textContent = '共 200 张';
}

test('workbench opens all records for its date and retains only page-size and density preferences', async () => {
  const h = harness(); applyOldScope(h);
  assert.equal(h.controller.openDayRecords('2026-09-09'), true);
  assert.equal(h.environment.location.hash, 'records');
  assert.equal(h.fields.import_date.value, '2026-09-09');
  assert.equal(h.fields.import_date.defaultValue, '2026-09-09');
  assert.match(h.$('#calendar-toggle').textContent, /2026\/09\/09/);
  for (const [key, field] of Object.entries(h.fields)) {
    if (!['import_date', 'text_match'].includes(key)) assert.equal(field.value, '', key);
  }
  assert.equal(h.fields.text_match.value, 'prefix');
  assert.equal(h.recordsState.batchFilter, '');
  assert.equal(h.$('#batch-scope').classList.contains('hidden'), true);
  assert.deepEqual(h.tabs.map(tab => tab.classList.contains('active')), [true, false, false]);
  assert.equal(h.recordsState.selected.size, 0);
  assert.equal(h.recordsState.recordPage, 1);
  assert.equal(h.recordsState.recordPageSize, 25);
  assert.equal(h.recordsState.compact, true);
  assert.equal(h.recordsState.recordsLoaded, false);
  assert.equal(h.$('#record-count').textContent, '');
  assert.doesNotMatch(h.$('#records-body').innerHTML, /旧回单/);
  assert.doesNotMatch(h.$('#records-page-info').textContent, /第 4/);
  assert.equal(h.requests.length, 0, 'navigation owns the query');
  await h.controller.loadRecords({background: h.recordsState.recordsLoaded});
  const query = new URL(h.requests[0].url, 'http://local').searchParams;
  assert.equal(query.get('import_date'), '2026-09-09');
  assert.equal(query.get('task_id'), '');
  assert.equal(query.get('search'), '');
  assert.equal(query.get('page'), '1');
  assert.equal(query.get('page_size'), '25');
});

test('entering the day cancels pending searches and form-reset queries before navigation loads once', async () => {
  const h = harness(); applyOldScope(h);
  h.controller.scheduleRecordSearch();
  await h.$('#filters').emit('reset');
  assert.equal(h.timers.size, 2);
  h.controller.openDayRecords('2026-09-10');
  await h.flushTimers();
  assert.equal(h.requests.length, 0);
  await h.controller.loadRecords();
  assert.equal(h.requests.length, 1);
  assert.equal(h.fields.task_id.value, '');
});

test('an old search response cannot restore old rows, selection, counts or pagination during the day transition', async () => {
  const h = harness(); applyOldScope(h);
  let resolveOld;
  h.respond(() => new Promise(resolve => { resolveOld = resolve; }));
  const oldRequest = h.controller.loadRecords();
  const oldSignal = h.requests[0].options.signal;
  h.controller.openDayRecords('2026-09-10');
  assert.equal(oldSignal.aborted, true);
  resolveOld({items: [{id: 91, filename: '旧回单.jpg'}], total: 200, page: 1, page_size: 25});
  await oldRequest;
  assert.deepEqual(h.recordsState.records, []);
  assert.equal(h.recordsState.recordsLoaded, false);
  assert.equal(h.$('#record-count').textContent, '');
  assert.equal(h.$('#records-page-info').textContent, '正在加载…');
  assert.equal(h.$('#records-body').inert, true);
  assert.equal(h.fields.task_id.value, '');
  h.respond(async () => ({items: [], total: 0, page: 1, page_size: 25}));
  await h.controller.loadRecords();
  assert.equal(h.$('#record-count').textContent, '共 0 张');
  assert.equal(h.$('#records-body').inert, false);
});

test('invalid workbench dates preserve the current query and navigation', () => {
  for (const day of ['', '2026-02-29', '2026-09-31', '2026-13-01', '2026-00-01', '2026-09-00', '0000-01-01', '2026-9-10', '2026-09-10<script>', undefined]) {
    const h = harness(); applyOldScope(h);
    assert.equal(h.controller.openDayRecords(day), false, String(day));
    assert.equal(h.environment.location.hash, '#progress');
    assert.equal(h.fields.import_date.value, '2026-08-11');
    assert.equal(h.recordsState.batchFilter, 'old-batch');
    assert.deepEqual([...h.recordsState.selected], [91]);
    assert.equal(h.requests.length, 0);
    assert.match(h.notices[0], /有效.*日期/);
  }
  assert.equal(harness().controller.openDayRecords('2024-02-29'), true);
});
