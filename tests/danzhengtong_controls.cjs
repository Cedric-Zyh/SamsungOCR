const assert = require('node:assert/strict');
const test = require('node:test');
const {remoteAction, remoteResultMarkup, createDanzhengtongControls} = require('../static/modules/workbench/danzhengtong.mjs');
const {ReceiptQueue} = require('../static/modules/core/queue.mjs');

function node() {
  const events = new Map();
  return {dataset: {}, attributes: {}, textContent: '', disabled: false, isConnected: true,
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(kind, callback) { events.set(kind, callback); },
    async click(target = this) { await events.get('click')?.({target, currentTarget: this, preventDefault() {}}); }};
}

test('pending provider uses query, completed provider uses view, values are escaped', () => {
  assert.match(remoteAction({id: 'new', status: 'succeeded'}), /提交单证通/);
  const pending = {id: '<request>', status: 'succeeded', danzhengtong: {status: 'waiting', label: '等待结果'}};
  assert.match(remoteAction(pending), /data-dzt-action="query"/);
  assert.match(remoteAction(pending), /&lt;request&gt;/);
  pending.danzhengtong.querying = true;
  assert.match(remoteAction(pending), /正在查询/);
  assert.match(remoteAction(pending), /disabled/);
  pending.danzhengtong = {status: 'applied', label: '已完成'};
  assert.match(remoteAction(pending), /data-dzt-action="view"/);
  const rows = ReceiptQueue.rows([{id: 1}], [{id: 'local', result_id: 1, status: 'succeeded',
    danzhengtong: {pending: true, status: 'waiting'}}]);
  assert.equal(rows[0].status, 'succeeded');
  assert.equal(ReceiptQueue.counts(rows).completed, 1);
  assert.equal(ReceiptQueue.counts(rows).waiting_remote, 1);
});

test('manual query respects selection and only enqueues querying the original task', async () => {
  const nodes = new Map(), document = node(), calls = [];
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  document.querySelectorAll = () => [];
  const state = {queueJobs: new Map([
    ['first', {id: 'first', result_id: 1, danzhengtong: {status: 'waiting'}}],
    ['second', {id: 'second', result_id: 2, danzhengtong: {status: 'waiting'}}],
  ]), dztControl: {auto_query: true, submit_paused: false}};
  let refreshes = 0;
  const controller = createDanzhengtongControls({environment: {document}, ui: {$, toast() {}},
    services: {danzhengtong: {action: async (...args) => calls.push(args),
      control: async changes => ({...state.dztControl, ...changes})}}, progressState: state,
    recordsState: {selected: new Set([2])}, refresh: async () => refreshes++});
  controller.initialize(); controller.render();
  assert.equal($('#dzt-query-all').disabled, false);
  await $('#dzt-query-all').click();
  assert.deepEqual(calls, [[['second'], 'query']]);
  assert.equal(refreshes, 1);
  await $('#dzt-query-toggle').click();
  assert.equal(state.dztControl.auto_query, false);
  assert.equal($('#dzt-query-toggle').attributes['aria-checked'], 'false');
  assert.equal($('#dzt-auto-note').textContent, '手动查询');
});

test('a queued sibling does not hide manual querying of an accepted branch', async () => {
  const job = {id: 'mixed', status: 'succeeded', danzhengtong: {
    status: 'queued', label: '等待提交', queryable: true}};
  assert.match(remoteAction(job), /data-dzt-action="query"/);
  assert.match(remoteAction(job, {compact: true}), /data-dzt-action="query"/);
  const nodes = new Map(), document = node(), calls = [];
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  document.querySelectorAll = () => [];
  const controller = createDanzhengtongControls({environment: {document}, ui: {$, toast() {}},
    services: {danzhengtong: {action: async (...args) => calls.push(args)}},
    progressState: {queueJobs: new Map([[job.id, job]]), dztControl: {auto_query: false, submit_paused: true}},
    refresh: async () => {}});
  controller.initialize();
  assert.equal($('#dzt-query-all').disabled, false);
  await $('#dzt-query-all').click();
  assert.deepEqual(calls, [[['mixed'], 'query']]);
});

test('day-level remote querying includes every import despite a stale task batch', async () => {
  const nodes = new Map(), document = node(), calls = [];
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  document.querySelectorAll = () => [];
  const jobs = [
    {id: 'a', task_id: 'first', danzhengtong: {status: 'waiting', queryable: true}},
    {id: 'b', task_id: 'second', danzhengtong: {status: 'waiting', queryable: true}},
  ];
  const controller = createDanzhengtongControls({environment: {document}, ui: {$, toast() {}},
    services: {danzhengtong: {action: async (...args) => calls.push(args)}},
    progressState: {taskBatch: 'first', queueJobs: new Map(jobs.map(job => [job.id, job]))},
    refresh: async () => {}});
  controller.initialize();
  await $('#dzt-query-all').click();
  assert.deepEqual(calls, [[['a', 'b'], 'query']]);
});

function sealResult(index, text, status = 'completed') {
  return {index, status, req_uuid: `seal-${index}`, result: {commitResult: {
    '收货客户印章': {value: text}, '印章颜色': {value: index ? '红色' : '蓝色'},
    '印章类型': {value: index ? '圆章' : '椭圆章'}}}};
}

test('seal-only results show each dedicated stamp with its attributes, status and request id', () => {
  const html = remoteResultMarkup({label: '已完成', seal_result: {version: 1, prepared: true,
    requests: [sealResult(0, '北京恒远恒信科技发展有限公司 收发货专用章'), sealResult(2, '其他公司收货章')]}});
  assert.match(html, /印章专用场景/);
  assert.match(html, /印章 1 · 已返回/);
  assert.match(html, /印章 3 · 已返回/);
  assert.match(html, /北京恒远恒信科技发展有限公司 收发货专用章/);
  assert.match(html, /蓝色/);
  assert.match(html, /椭圆章/);
  assert.match(html, /红色/);
  assert.match(html, /圆章/);
  assert.match(html, /印章请求编号：seal-0/);
  assert.match(html, /印章请求编号：seal-2/);
  assert.doesNotMatch(html, /整页请求编号|尚未取得/);
});

test('mixed results retain page fields and dates while hiding the former page seal field', () => {
  const html = remoteResultMarkup({label: '已完成', req_uuid: 'page-request', result: {commitResult: {
    '客户名称': {value: '客户甲'}, '签收日期': {value: '2025-01-04'},
    '收货客户印章': {value: '旧整页印章'}, '拒收数量': {value: 0}}},
  seal_result: {version: 1, prepared: true, requests: [sealResult(0, '新印章文字')]}});
  assert.match(html, /客户甲/);
  assert.match(html, /2025-01-04/);
  assert.match(html, /<dt>拒收数量<\/dt><dd>0<\/dd>/);
  assert.match(html, /整页请求编号：page-request/);
  assert.match(html, /新印章文字/);
  assert.doesNotMatch(html, /旧整页印章/);
  assert.equal((html.match(/<dt>收货客户印章<\/dt>/g) || []).length, 1);
});

test('legacy results without a dedicated scene keep the original page seal value', () => {
  const html = remoteResultMarkup({label: '已完成', req_uuid: 'legacy', result: {commitResult: {
    '收货客户印章': {value: '历史整页文字'}}}, seal_result: {}});
  assert.match(html, /历史整页文字/);
  assert.match(html, /请求编号：legacy/);
  assert.doesNotMatch(html, /印章专用场景/);
});

test('v2 page-only state does not invent a dedicated seal scene', () => {
  const html = remoteResultMarkup({label: '已完成', req_uuid: 'page-request', page_req_uuid: 'page-request',
    result: {commitResult: {'客户名称': {value: '客户甲'}, '收货客户印章': {value: '历史整页文字'}}},
    seal_result: {version: 2, seal_required: false, page_required: true,
      page: {status: 'completed'}, prepared: true, requests: []}});
  assert.match(html, /整页识别 · 已返回/);
  assert.match(html, /客户甲/);
  assert.match(html, /历史整页文字/);
  assert.match(html, /整页请求编号：page-request/);
  assert.doesNotMatch(html, /印章专用场景|未检测到可提交单证通的印章/);
});

test('v2 failed page and completed seal keep branch status and UUID independent', () => {
  const html = remoteResultMarkup({label: '已完成', req_uuid: 'seal-0', page_req_uuid: '',
    seal_result: {version: 2, seal_required: true, page_required: true, prepared: true,
      page: {status: 'unknown', error: '<整页提交状态待确认>'}, requests: [sealResult(0, '客户章')]}});
  assert.match(html, /整页识别 · 提交状态待确认/);
  assert.match(html, /&lt;整页提交状态待确认&gt;/);
  assert.match(html, /整页请求编号：尚未取得/);
  assert.match(html, /印章 1 · 已返回/);
  assert.match(html, /印章请求编号：seal-0/);
  assert.doesNotMatch(html, /整页请求编号：seal-0|<整页提交状态待确认>/);
});

test('v2 seal-only state never labels the fallback stamp UUID as a page UUID', () => {
  const html = remoteResultMarkup({label: '已完成', req_uuid: 'seal-0', page_req_uuid: '',
    seal_result: {version: 2, seal_required: true, page_required: false,
      page: {status: 'skipped'}, prepared: true, requests: [sealResult(0, '客户章')]}});
  assert.match(html, /印章请求编号：seal-0/);
  assert.doesNotMatch(html, /整页识别|整页请求编号/);
});

test('v2 preparation failure is shown as a seal failure while keeping page evidence', () => {
  const html = remoteResultMarkup({label: '已完成', req_uuid: 'page', page_req_uuid: 'page',
    result: {commitResult: {'客户名称': {value: '客户甲'}}},
    seal_result: {version: 2, seal_required: true, page_required: true, page: {status: 'completed'},
      prepared: true, preparation_error: '<裁图失败>', requests: []}});
  assert.match(html, /客户甲/);
  assert.match(html, /&lt;裁图失败&gt;/);
  assert.match(html, /印章准备失败/);
  assert.doesNotMatch(html, /未检测到可提交单证通的印章|<裁图失败>/);
});

test('pending, failed and empty seal results remain readable and all returned values are escaped', () => {
  const html = remoteResultMarkup({label: '<已返回>', error_message: '<顶层错误>',
    result: {commitResult: {'收货客户印章': {value: '不能显示的旧印章'}}},
    seal_result: {prepared: true, error: '<准备错误>', requests: [
      {index: 0, status: 'waiting', req_uuid: '<request>', result: {commitResult: {}}},
      {index: 1, status: 'failed', error: '<网络错误>'},
      {index: 2, status: '<unknown>', result: {commitResult: {
        '收货客户印章': {value: '<img src=x onerror=alert(1)>'},
        '印章颜色': {value: ''}, '印章类型': {value: null}}}},
    ]}});
  assert.match(html, /印章 1 · 等待结果/);
  assert.match(html, /印章 2 · 识别失败/);
  assert.match(html, /印章 3 · &lt;unknown&gt;/);
  assert.match(html, /&lt;网络错误&gt;/);
  assert.match(html, /&lt;准备错误&gt;/);
  assert.match(html, /&lt;顶层错误&gt;/);
  assert.match(html, /&lt;img/);
  assert.match(html, /印章请求编号：&lt;request&gt;/);
  assert.match(html, /未识别/);
  assert.doesNotMatch(html, /不能显示的旧印章|<img|<unknown>|<网络错误>/);
  assert.match(remoteResultMarkup({seal_result: {prepared: true, requests: []}}), /未检测到可提交单证通的印章/);
  assert.match(remoteResultMarkup({seal_result: {prepared: false}}), /印章尚未准备完成/);
});

test('view action opens the dedicated results in the existing dialog without submitting a new request', async () => {
  const nodes = new Map(), document = node(), calls = [];
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const dialog = $('#dzt-result-dialog');
  dialog.showModal = () => { dialog.open = true; };
  document.querySelectorAll = () => [];
  const controller = createDanzhengtongControls({environment: {document}, ui: {$, toast() {}},
    services: {danzhengtong: {detail: async id => {
      calls.push(id);
      return {seal_result: {prepared: true, requests: [sealResult(0, '弹窗中的新印章')]}};
    }, action: async () => assert.fail('view must not submit or query')}}, progressState: {}, refresh: async () => {}});
  controller.initialize();
  const button = node(); button.dataset = {dztAction: 'view', dztJob: 'job-one'};
  await document.click({closest: selector => selector === '[data-dzt-action]' ? button : null});
  assert.deepEqual(calls, ['job-one']);
  assert.equal(dialog.open, true);
  assert.equal(dialog.dataset.jobId, 'job-one');
  assert.match($('#dzt-result-body').innerHTML, /弹窗中的新印章/);
});

function delayedViews() {
  const nodes = new Map(), document = node(), requests = new Map(), calls = [], events = new Map();
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const dialog = $('#dzt-result-dialog');
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  document.querySelectorAll = () => [];
  createDanzhengtongControls({environment: {document, window: {addEventListener: (name, fn) => events.set(name, fn)}},
    ui: {$, toast: message => calls.push(['toast', message])},
    services: {danzhengtong: {detail: id => new Promise((resolve, reject) => requests.set(id, {resolve, reject})),
      action: async (...args) => calls.push(args)}}, progressState: {}, refresh: async () => {}}).initialize();
  const view = id => {
    const button = node(); button.dataset = {dztAction: 'view', dztJob: id};
    return document.click({closest: selector => selector === '[data-dzt-action]' ? button : null});
  };
  return {$, dialog, requests, calls, view, navigate: () => events.get('hashchange')()};
}

test('late view responses never replace the latest receipt or its resubmit target', async () => {
  const h = delayedViews(), first = h.view('older'), latest = h.view('latest');
  h.requests.get('latest').resolve({label: 'latest result'}); await latest;
  h.requests.get('older').resolve({label: 'older result'}); await first;
  assert.equal(h.dialog.dataset.jobId, 'latest');
  assert.match(h.$('#dzt-result-body').innerHTML, /latest result/);
  assert.doesNotMatch(h.$('#dzt-result-body').innerHTML, /older result/);
  await h.$('#dzt-resubmit').click();
  assert.deepEqual(h.calls.filter(call => call[0] !== 'toast'), [[['latest'], 'resubmit']]);
});

test('leaving or closing a remote view invalidates outstanding responses and old errors', async () => {
  const h = delayedViews(), old = h.view('old'), next = h.view('next');
  h.requests.get('old').reject(Error('obsolete')); await old;
  h.navigate();
  h.requests.get('next').resolve({label: 'next result'}); await next;
  assert(!h.dialog.open);
  assert.equal(h.dialog.dataset.jobId, undefined);
  assert.deepEqual(h.calls, []);
  const ready = h.view('ready');
  h.requests.get('ready').resolve({label: 'ready result'}); await ready;
  const pending = h.view('pending');
  await h.$('#dzt-result-close').click();
  h.requests.get('pending').resolve({label: 'pending result'}); await pending;
  assert(!h.dialog.open);
  assert(h.$('#dzt-resubmit').disabled);
});
