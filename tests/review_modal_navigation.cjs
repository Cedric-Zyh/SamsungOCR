const assert = require('node:assert/strict');
const test = require('node:test');
const {createNavigation} = require('../static/modules/shell/navigation.mjs');
const {createReview} = require('../static/modules/review/review.mjs');
const {createReviewQueue} = require('../static/modules/review/review_queue.mjs');
const {createState} = require('../static/modules/core/state.mjs');
const {ReceiptWorkbench} = require('../static/modules/core/workbench.mjs');
const {createSampleStatistics} = require('../static/modules/samples/statistics.mjs');

// Production controllers share their real state; only browser surfaces and I/O
// are replaced so these tests exercise close/navigation races without storage.
function element(document, tagName = 'DIV') {
  const classes = new Set(), listeners = new Map();
  return {
    tagName, dataset: {}, attributes: {}, children: [], parentNode: null, parentElement: null,
    value: '', textContent: '', innerHTML: '', disabled: false, hidden: false, inert: false, open: false, isConnected: true,
    scrollTop: 0, scrollLeft: 0, style: {setProperty() {}}, showCount: 0, closeCount: 0,
    classList: {
      add: (...values) => values.forEach(value => classes.add(value)),
      remove: (...values) => values.forEach(value => classes.delete(value)),
      contains: value => classes.has(value),
      toggle(value, enabled = !classes.has(value)) { if (enabled) classes.add(value); else classes.delete(value); return enabled; },
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] ?? null; },
    removeAttribute(name) { delete this.attributes[name]; },
    append(...nodes) {
      for (const node of nodes) {
        if (node.parentNode) node.parentNode.children = node.parentNode.children.filter(child => child !== node);
        this.children.push(node); node.parentNode = node.parentElement = this;
      }
    },
    appendChild(node) { this.append(node); return node; },
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); },
    contains(node) { return node === this || this.children.some(child => child.contains(node)); },
    scrollTo(x, y) { this.scrollLeft = x; this.scrollTop = y; },
    focus() { document.activeElement = this; },
    closest(selector) {
      for (let node = this; node; node = node.parentElement) {
        if (selector === '[data-open-review]' && node.dataset.openReview) return node;
        if (selector === '[data-page="records"]' && node.dataset.page === 'records') return node;
      }
      return null;
    },
    showModal() { assert.equal(this.open, false); this.open = true; this.showCount++; },
    close() {
      if (!this.open) return;
      this.open = false; this.closeCount++;
      // Native dialog close is dispatched later, after routing has continued.
      setImmediate(() => this.emit('close'));
    },
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    async emit(name, props = {}) {
      const event = {target: this, currentTarget: this, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...props};
      // DOM listeners run synchronously in registration order; do not flush
      // microtasks between statistics and navigation hashchange handlers.
      await Promise.all((listeners.get(name) || []).map(callback => callback(event)));
      return event;
    },
  };
}

function harness({hash = '#records', loaded = true} = {}) {
  const document = {activeElement: null, title: ''};
  Object.assign(document, element(document));
  document.body = element(document, 'BODY'); document.body.dataset.activePage = 'records';
  const nodes = new Map(), notices = [], historyCalls = [], loads = [], opened = [];
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element(document));
    return nodes.get(selector);
  };
  const pages = ['progress', 'records', 'quality', 'settings', 'samples', 'seal-center', 'review'].map(page => {
    const node = $(`[data-page="${page}"]`); node.dataset.page = page; return node;
  });
  const links = ['progress', 'records', 'quality', 'settings', 'samples', 'seal-center'].map(page => {
    const node = $(`[data-nav="${page}"]`); node.dataset.nav = page; return node;
  });
  const dialog = $('#review-dialog'); dialog.tagName = 'DIALOG';
  document.body.append(...pages.filter(node => node.dataset.page !== 'review'), $('#toast'), dialog);
  dialog.append($('#review-close'), $('[data-page="review"]'));
  const opener = $('[data-open-review="41"]'); opener.dataset.openReview = '41';
  $('[data-page="records"]').append(opener, $('#record-search')); document.activeElement = opener;
  $('#review-template').content = {cloneNode: () => element(document)};
  $('.review-workspace').append($('[data-review-divider]'));
  const $$ = selector => selector === '[data-page]' ? pages : selector === '[data-nav]' ? links
    : selector === '[data-close-modal]' ? [$('#review-close')] : [];
  const location = {hash};
  const history = {
    replaceState(_state, _title, next) { historyCalls.push(['replace', next]); location.hash = next; },
    pushState(_state, _title, next) { historyCalls.push(['push', next]); location.hash = next; },
  };
  const window = {...element(document), scrollY: 75, scrollX: 0, confirm: () => false,
    scrollTo(x, y) { this.scrollX = x; this.scrollY = y; },
    requestAnimationFrame(callback) { callback(); },
  };
  const environment = {document, window, location, history, AbortController, URLSearchParams,
    setTimeout: callback => callback(), clearTimeout() {}, localStorage: {getItem() { return null; }, setItem() {}},
  };
  const ui = {$, $$, toast: message => notices.push(message)}, state = createState();
  state.records.recordsLoaded = loaded;
  const table = $('.record-filter-table'); table.scrollTop = 430; table.scrollLeft = 80;
  $('[data-page="records"]').append(table);
  const behavior = {api: async () => { throw Error('Unexpected request'); }, renderHistory: async () => {}, loadReviewQueue: async () => {}, loadSealCenter: async () => {}};
  let navigation;
  const review = createReview({environment, ui, navigationState: state.navigation, reviewState: state.review,
    api: (...args) => behavior.api(...args), ReceiptWorkbench, fieldSchema: {printed: [], handwritten: []},
    fieldEditor: () => '', renderProductTable() {}, renderArtifacts() {},
    renderHistory: (...args) => behavior.renderHistory(...args), renderGroundTruth: async () => {}, ensureReviewScope() {},
    loadReviewQueue: (...args) => behavior.loadReviewQueue(...args),
    routePage: () => navigation.routePage(), showPage: page => navigation.showPage(page),
  });
  const options = {
    environment, ui, navigationState: state.navigation, recordsState: state.records,
    reportState: state.report, reviewState: state.review,
    canLeaveReview: args => review.canLeaveReview(args),
    dismissReview: () => review.dismissReview(),
    showReviewEmpty: (...args) => review.showReviewEmpty(...args),
    openReview: async id => { opened.push(id); state.review.current = {id}; navigation.showPage('review'); },
    showImportDialog: () => $('#import-dialog').showModal(),
    loadReviewQueue: (...args) => behavior.loadReviewQueue(...args), loadDailyResults: async () => {}, loadReport: async () => {},
    loadRecords: async options => { loads.push(options); state.records.recordsLoaded = true; },
    loadSealCenter: (...args) => behavior.loadSealCenter(...args),
  };
  navigation = createNavigation(options);
  return {document, window, location, historyCalls, nodes, $, dialog, opener, table,
    state, review, navigation, options, loads, notices, opened, environment, ui, behavior};
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

for (const sample of [false, true]) {
  test(`seal source ${sample ? 'sample' : 'receipt'} opens above its archive and closes back to the refreshed source button`, async () => {
    const h = harness({hash: '#seal-center', loaded: false}), requests = [];
    h.navigation.showPage('seal-center'); h.window.scrollY = 75;
    const source = h.$('[data-standard-source]'); source.tagName = 'BUTTON';
    source.dataset.standardSource = '41'; source.dataset.sourceKind = sample ? 'sample' : 'receipt';
    h.$('[data-page="seal-center"]').append(source); source.focus();
    h.behavior.api = async route => { requests.push(route); return {id: 41, filename: 'source.jpg', fields: {}}; };
    let refreshed = 0, replacement;
    h.behavior.loadSealCenter = async () => {
      refreshed++;
      replacement = element(h.document, 'BUTTON');
      replacement.dataset.standardSource = '41';
      source.isConnected = false; h.document.activeElement = h.document.body;
      h.nodes.set('[data-standard-source]', replacement);
      h.$('[data-page="seal-center"]').append(replacement);
    };
    await h.review.openReview(41, {sample, mode: 'view', origin: '#seal-center'});
    assert.deepEqual(requests, [sample ? '/api/samples/results/41' : '/api/results/41']);
    assert.equal(h.location.hash, sample ? '#sample-view/41' : '#view/41');
    assert.equal(h.state.review.sampleView, sample);
    assert.equal(h.state.review.reviewMode, 'view');
    assert.equal(h.state.review.reviewOrigin.hash, '#seal-center');
    assert.equal(h.dialog.open, true);
    assert.equal(h.$('[data-page="seal-center"]').classList.contains('hidden'), false);
    assert.equal(h.$('[data-page="records"]').classList.contains('hidden'), true);
    assert.equal(h.$('[data-page="samples"]').classList.contains('hidden'), true);
    assert.equal(h.$('[data-nav="seal-center"]').getAttribute('aria-current'), 'page');
    assert.equal(h.$('#page-title').textContent, '印章中心');
    assert.equal(h.state.navigation.reviewReturn.focus, source);
    assert.deepEqual(h.loads, []);
    h.$('#review-close').focus(); h.window.scrollY = 0;
    h.review.closeModal(); await settle();
    assert.equal(h.location.hash, '#seal-center');
    assert.equal(h.dialog.open, false);
    assert.equal(h.state.review.current, null);
    assert.equal(h.document.body.dataset.activePage, 'seal-center');
    assert.equal(h.$('[data-nav="seal-center"]').getAttribute('aria-current'), 'page');
    assert.equal(h.document.activeElement, replacement);
    assert.equal(h.window.scrollY, 75);
    assert.equal(refreshed, 1);
    assert.deepEqual(h.loads, []);
  });

  test(`browser Back from a seal source ${sample ? 'sample' : 'receipt'} returns to the archive without loading records`, async () => {
    const h = harness({hash: '#seal-center', loaded: false});
    h.navigation.showPage('seal-center');
    const source = h.$('[data-standard-source]');
    h.$('[data-page="seal-center"]').append(source); source.focus();
    h.behavior.api = async () => ({id: 41, filename: 'source.jpg', fields: {}});
    await h.review.openReview(41, {sample, mode: 'view', origin: '#seal-center'});
    h.$('#review-close').focus();
    h.location.hash = '#seal-center'; h.navigation.routePage(); await settle();
    assert.equal(h.dialog.open, false);
    assert.equal(h.document.activeElement, source);
    assert.equal(h.state.review.current, null);
    assert.equal(h.$('[data-page="seal-center"]').classList.contains('hidden'), false);
    assert.equal(h.$('[data-nav="seal-center"]').getAttribute('aria-current'), 'page');
    assert.deepEqual(h.loads, []);
  });
}

test('a delayed archive refresh does not steal focus after the user selects another control', async () => {
  const h = harness({hash: '#seal-center'});
  h.navigation.showPage('seal-center');
  const source = h.$('[data-standard-source]');
  h.$('[data-page="seal-center"]').append(source); source.focus();
  h.behavior.api = async () => ({id: 41, filename: 'source.jpg', fields: {}});
  let resolve;
  h.behavior.loadSealCenter = () => new Promise(done => { resolve = done; });
  await h.review.openReview(41, {mode: 'view', origin: '#seal-center'});
  h.review.closeModal();
  const search = h.$('#seal-center-search'); h.$('[data-page="seal-center"]').append(search); search.focus();
  resolve(); await settle();
  assert.equal(h.document.activeElement, search);
});

test('sample viewing outside the seal archive keeps the existing samples backdrop and return route', async () => {
  const h = harness({hash: '#samples', loaded: false});
  h.navigation.showPage('samples');
  h.behavior.api = async () => ({id: 41, filename: 'sample.jpg', fields: {}});
  await h.review.openReview(41, {sample: true, mode: 'view'});
  assert.equal(h.state.review.reviewOrigin.hash, '#samples');
  assert.equal(h.$('[data-page="samples"]').classList.contains('hidden'), false);
  assert.equal(h.$('[data-page="seal-center"]').classList.contains('hidden'), true);
  assert.equal(h.$('[data-nav="samples"]').getAttribute('aria-current'), 'page');
  h.review.closeModal();
  assert.equal(h.location.hash, '#samples');
  assert.equal(h.dialog.open, false);
  assert.deepEqual(h.loads, []);
});

test('review opens above the records page, keeps the records navigation selected, and mounts feedback inside the dialog', () => {
  const h = harness(); h.state.review.reviewOrigin = {hash: '#records', scroll: 75};
  h.navigation.showPage('review');
  assert.equal(h.dialog.open, true);
  assert.equal(h.document.body.dataset.activePage, 'review');
  assert.equal(h.$('[data-page="records"]').classList.contains('hidden'), false);
  assert.equal(h.$('[data-page="review"]').classList.contains('hidden'), false);
  assert.equal(h.$('[data-page="progress"]').classList.contains('hidden'), true);
  assert.equal(h.$('[data-nav="records"]').getAttribute('aria-current'), 'page');
  assert.equal(h.$('#page-title').textContent, '回单管理');
  assert.equal(h.$('#toast').parentElement, h.dialog);
  assert.equal(h.document.body.classList.contains('review-dialog-open'), true);
  h.$('#review-close').focus(); h.table.scrollTop = 0;
  h.navigation.showPage('review');
  assert.equal(h.dialog.showCount, 1);
  assert.equal(h.state.navigation.reviewReturn.focus, h.opener);
  assert.equal(h.state.navigation.reviewReturn.tableTop, 430);
  assert.equal(h.window.scrollY, 75);
});

test('browser Back preserves unsaved edits and restores the review URL without creating another history entry', () => {
  const h = harness(); h.state.review.current = {id: 41};
  h.navigation.showPage('review'); h.state.review.reviewDirty = true;
  h.location.hash = '#records'; h.navigation.routePage();
  assert.equal(h.location.hash, '#review/41');
  assert.deepEqual(h.historyCalls, [['replace', '#review/41']]);
  assert.equal(h.dialog.open, true);
  assert.equal(h.state.review.current.id, 41);
  assert.equal(h.state.review.reviewDirty, true);
  assert.equal(h.loads.length, 0);
});

test('accepted navigation closes the dialog, invalidates pending review work and restores the record position and feedback host', () => {
  const h = harness(); h.state.review.current = {id: 41};
  h.state.review.reviewOrigin = {hash: '#records', scroll: 75};
  Object.assign(h.state.review, {reviewIntent: 10, reviewRequest: 20, queueRequest: 30});
  const controller = h.state.review.reviewQueueController = new AbortController();
  h.navigation.showPage('review');
  const replacement = element(h.document, 'BUTTON'); replacement.dataset.openReview = '41';
  h.$('[data-page="records"]').append(replacement);
  h.opener.isConnected = false; h.nodes.set('[data-open-review="41"]', replacement);
  h.table.scrollTop = 0; h.table.scrollLeft = 0; h.window.scrollY = 0;
  h.location.hash = '#records'; h.navigation.routePage();
  assert.equal(h.dialog.open, false);
  assert.equal(h.state.review.current, null);
  assert.equal(controller.signal.aborted, true);
  assert(h.state.review.reviewIntent > 10 && h.state.review.reviewRequest > 20 && h.state.review.queueRequest > 30);
  assert.equal(h.table.scrollTop, 430); assert.equal(h.table.scrollLeft, 80); assert.equal(h.window.scrollY, 75);
  assert.equal(h.document.activeElement, replacement);
  assert.equal(h.$('#toast').parentElement, h.document.body);
  assert.equal(h.document.body.classList.contains('review-dialog-open'), false);
  assert.deepEqual(h.loads, [{background: true}]);
});

test('direct review URLs load a records backdrop and close to records when there is no prior list page', async () => {
  const h = harness({hash: '#review/41', loaded: false});
  h.document.body.dataset.activePage = 'progress'; h.navigation.routePage(); await settle();
  assert.deepEqual(h.opened, [41]); assert.equal(h.dialog.open, true);
  assert.equal(h.loads.length, 1);
  assert.equal(h.$('[data-page="records"]').classList.contains('hidden'), false);
  h.review.closeModal();
  assert.equal(h.location.hash, '#records'); assert.equal(h.dialog.open, false);
  assert.equal(h.document.body.dataset.activePage, 'records');
});

test('native Escape cancellation closes empty and loading reviews but cannot discard edits or interrupt a save', async () => {
  const h = harness(); h.review.initialize();
  h.location.hash = '#review'; h.navigation.showPage('review');
  let event = await h.dialog.emit('cancel');
  assert.equal(event.defaultPrevented, true); assert.equal(h.dialog.open, false); assert.equal(h.location.hash, '#records');
  h.location.hash = '#review/41'; h.state.review.current = {id: 41}; h.navigation.showPage('review');
  h.state.review.reviewDirty = true;
  event = await h.dialog.emit('cancel');
  assert.equal(event.defaultPrevented, true); assert.equal(h.dialog.open, true); assert.equal(h.state.review.reviewDirty, true);
  h.state.review.reviewDirty = false; h.state.review.reviewSaving = true;
  await h.dialog.emit('cancel');
  assert.equal(h.dialog.open, true); assert.match(h.notices.at(-1), /正在保存/);
  h.state.review.reviewSaving = false;
  await h.dialog.emit('cancel');
  assert.equal(h.dialog.open, false);
});

test('closing while receipt detail is loading prevents its late response from reopening the dialog', async () => {
  const h = harness(); let resolve;
  h.behavior.api = () => new Promise(done => { resolve = done; });
  h.location.hash = '#review'; h.navigation.showPage('review');
  const pending = h.review.openReview(41);
  h.review.closeModal(); resolve({id: 41}); await pending;
  assert.equal(h.dialog.open, false); assert.equal(h.state.review.current, null); assert.equal(h.location.hash, '#records');
});

test('closing a loading queue invalidates the session and prevents a late queue response from opening a receipt', async () => {
  const h = harness(); let resolve;
  const queue = createReviewQueue({environment: h.environment, ui: h.ui, reviewState: h.state.review,
    ReceiptWorkbench: {issues: () => [], nextReview: rows => rows[0]},
    api: () => new Promise(done => { resolve = done; }),
    canLeaveReview: () => h.review.canLeaveReview(), openReview: h.options.openReview,
    showReviewEmpty: (...args) => h.review.showReviewEmpty(...args), showPage: page => h.navigation.showPage(page),
  });
  const pending = queue.startReviewScope({kind: 'filtered', filters: {customer: '甲客户'}});
  assert.equal(h.dialog.open, true); assert.equal(h.location.hash, '#review');
  assert.deepEqual(h.historyCalls, [['push', '#review']]);
  h.review.closeModal();
  resolve({items: [{id: 41, filename: '41.jpg'}], total: 1, page: 1, page_size: 100});
  assert.equal(await pending, false);
  assert.equal(h.dialog.open, false); assert.deepEqual(h.opened, []); assert.equal(h.location.hash, '#records');
});

for (const recordId of [41, undefined]) {
  test(`initial ${recordId ? 'selected receipt' : 'next receipt'} detail failure replaces loading with a dismissible error`, async () => {
    const h = harness(); h.review.initialize();
    h.behavior.api = async () => { throw Error('回单详情暂时无法读取'); };
    const queue = createReviewQueue({environment: h.environment, ui: h.ui, reviewState: h.state.review,
      ReceiptWorkbench: {issues: () => [], nextReview: rows => rows[0]},
      api: async () => ({items: [{id: 41, filename: '41.jpg'}], total: 1, page: 1, page_size: 100}),
      canLeaveReview: () => h.review.canLeaveReview(), openReview: id => h.review.openReview(id),
      showReviewEmpty: (...args) => h.review.showReviewEmpty(...args), showPage: page => h.navigation.showPage(page),
    });
    const opening = queue.startReviewScope({kind: 'filtered', filters: {customer: '甲客户'}}, {recordId});
    assert.equal(h.$('#review-empty').getAttribute('aria-busy'), 'true');
    await assert.rejects(opening, /回单详情暂时无法读取/);
    assert.equal(h.dialog.open, true); assert.equal(h.state.review.current, null);
    assert.equal(h.$('#review-modal').classList.contains('hidden'), true);
    assert.equal(h.$('#review-empty').getAttribute('aria-busy'), 'false');
    assert.equal(h.$('#review-empty').classList.contains('has-error'), true);
    assert.equal(h.$('#review-empty p').textContent, '回单详情暂时无法读取');
    await h.dialog.emit('cancel');
    assert.equal(h.dialog.open, false); assert.equal(h.location.hash, '#records');
  });
}

test('bare review navigation hides a previous form while loading and shows a dismissible error when the queue fails', async () => {
  const h = harness(); h.review.initialize();
  h.state.review.current = {id: 41}; h.navigation.showPage('review');
  h.$('#review-modal').classList.remove('hidden'); h.$('#review-content').innerHTML = 'previous receipt form';
  h.review.closeModal();
  let reject;
  h.behavior.loadReviewQueue = () => new Promise((_resolve, fail) => { reject = fail; });
  h.location.hash = '#review'; h.navigation.routePage();
  assert.equal(h.dialog.open, true); assert.equal(h.state.review.current, null);
  assert.equal(h.$('#review-modal').classList.contains('hidden'), true);
  assert.equal(h.$('#review-empty').getAttribute('aria-busy'), 'true');
  assert.equal(h.$('#review-empty h2').textContent, '正在加载回单…');
  reject(Error('队列暂时无法读取')); await settle();
  assert.equal(h.$('#review-modal').classList.contains('hidden'), true);
  assert.equal(h.$('#review-empty').getAttribute('aria-busy'), 'false');
  assert.equal(h.$('#review-empty').classList.contains('has-error'), true);
  assert.equal(h.$('#review-empty p').textContent, '队列暂时无法读取');
  assert.equal(h.notices.at(-1), '队列暂时无法读取');
  await h.dialog.emit('cancel');
  assert.equal(h.dialog.open, false); assert.equal(h.location.hash, '#records');
});

async function sampleBrowseHarness(id = 42, browseIds = [41, 42, 43]) {
  const h = harness({hash: '#samples'});
  h.navigation.showPage('samples'); h.window.scrollY = 75; h.review.initialize();
  const requests = [];
  h.behavior.api = async route => {
    requests.push(route);
    const currentId = Number(route.match(/results\/(\d+)/)[1]);
    return {id: currentId, filename: `${currentId}.jpg`, fields: {}};
  };
  await h.review.openReview(id, {sample: true, browseIds});
  return {...h, requests};
}

test('sample arrows follow source order, stop at both ends, and preserve one modal and its return position', async () => {
  const h = await sampleBrowseHarness(42, [43, 42, 41]);
  assert.equal(h.$('#review-browse-position').textContent, '2 / 3');
  assert.equal(h.dialog.classList.contains('has-sample-browse'), true);
  assert.equal(h.$('#review-browse-prev').disabled, false);
  assert.equal(h.$('#review-browse-next').disabled, false);
  const origin = h.state.review.reviewOrigin, historyLength = h.historyCalls.length;
  await h.$('#review-browse-next').emit('click');
  assert.equal(h.state.review.current.id, 41);
  assert.equal(h.location.hash, '#sample-view/41');
  assert.equal(h.$('#review-browse-position').textContent, '3 / 3');
  assert.equal(h.$('#review-browse-next').disabled, true);
  await h.$('#review-browse-next').emit('click');
  assert.equal(h.requests.length, 2);
  await h.$('#review-browse-prev').emit('click');
  await h.$('#review-browse-prev').emit('click');
  assert.equal(h.state.review.current.id, 43);
  assert.equal(h.$('#review-browse-prev').disabled, true);
  assert.equal(h.dialog.showCount, 1);
  assert.equal(h.state.review.reviewOrigin, origin);
  assert(h.historyCalls.slice(historyLength).every(([method]) => method === 'replace'));
  h.review.closeModal();
  assert.equal(h.location.hash, '#samples');
  assert.equal(h.window.scrollY, 75);
  assert.equal(h.dialog.classList.contains('has-sample-browse'), false);
});

test('cancelled unsaved sample browsing and saving guards retain the current edits and sample URL', async () => {
  const h = await sampleBrowseHarness();
  let prompts = 0;
  h.window.confirm = () => { prompts++; return false; };
  h.state.review.reviewDirty = true;
  await h.$('#review-browse-next').emit('click');
  assert.equal(prompts, 1);
  assert.equal(h.state.review.current.id, 42);
  assert.equal(h.state.review.reviewDirty, true);
  assert.equal(h.location.hash, '#sample-view/42');
  assert.equal(h.$('#review-browse-position').textContent, '2 / 3');
  assert.equal(h.requests.length, 1);
  h.state.review.reviewSaving = true;
  await h.$('#review-browse-prev').emit('click');
  assert.equal(h.requests.length, 1);
  assert.equal(h.notices.at(-1), '正在保存，请稍候');
  h.state.review.reviewSaving = false;
  h.window.confirm = () => true;
  await h.$('#review-browse-next').emit('click');
  assert.equal(h.state.review.current.id, 43);
  assert.equal(h.state.review.reviewDirty, false);
});

test('sample loading failure retains the current sample and allows retry while rapid clicks make only one request', async () => {
  const h = await sampleBrowseHarness();
  let reject, calls = 0;
  h.behavior.api = () => { calls++; return new Promise((_resolve, fail) => { reject = fail; }); };
  const moving = h.$('#review-browse-next').emit('click');
  assert.equal(h.$('#review-browse-prev').disabled, true);
  assert.equal(h.$('#review-browse-next').disabled, true);
  await h.$('#review-browse-next').emit('click');
  await h.$('#review-browse-prev').emit('click');
  assert.equal(calls, 1);
  reject(Error('暂时无法读取')); await moving;
  assert.equal(h.state.review.current.id, 42);
  assert.equal(h.location.hash, '#sample-view/42');
  assert.match(h.notices.at(-1), /样单加载失败：暂时无法读取/);
  assert.equal(h.$('#review-browse-next').disabled, false);
  h.behavior.api = async () => ({id: 43, filename: '43.jpg', fields: {}});
  await h.$('#review-browse-next').emit('click');
  assert.equal(h.state.review.current.id, 43);
});

test('new edits during sample loading cancel the switch without changing its route or browsing position', async () => {
  const h = await sampleBrowseHarness();
  let resolve;
  h.behavior.api = () => new Promise(done => { resolve = done; });
  const moving = h.$('#review-browse-next').emit('click');
  h.review.setReviewDirty();
  resolve({id: 43, filename: '43.jpg', fields: {}}); await moving;
  assert.equal(h.state.review.current.id, 42);
  assert.equal(h.state.review.reviewDirty, true);
  assert.equal(h.location.hash, '#sample-view/42');
  assert.equal(h.$('#review-browse-position').textContent, '2 / 3');
  assert.equal(h.$('#review-browse-next').disabled, false);
});

test('closing a pending sample switch prevents reopening, and a fresh source does not inherit its browse list', async () => {
  const h = await sampleBrowseHarness();
  let resolve;
  h.behavior.api = () => new Promise(done => { resolve = done; });
  const moving = h.$('#review-browse-next').emit('click');
  h.review.closeModal();
  resolve({id: 43, filename: '43.jpg', fields: {}}); await moving;
  assert.equal(h.state.review.current, null);
  assert.equal(h.dialog.open, false);
  assert.equal(h.location.hash, '#samples');
  h.behavior.api = async () => ({id: 42, filename: '42.jpg', fields: {}});
  await h.review.openReview(42, {sample: true});
  assert.equal(h.$('#review-browse-next').classList.contains('hidden'), true);
  assert.equal(h.$('#review-browse-prev').classList.contains('hidden'), true);
  assert.equal(h.$('#review-browse-position').textContent, '');
});

test('sample arrows retain the selected image region instead of returning to the first issue', async () => {
  const h = await sampleBrowseHarness();
  h.window.Image = class {};
  h.behavior.api = async route => {
    const id = Number(route.match(/results\/(\d+)/)[1]);
    return {id, filename: `${id}.jpg`, fields: {}, preview_url: `/files/${id}.jpg`,
      processing_artifacts: {date: [{original_url: `/files/${id}-date.jpg`}],
        seals: [{original_url: `/files/${id}-seal.jpg`}]}};
  };
  await h.review.openReview(42, {sample: true, browseIds: [41, 42, 43]});
  h.review.focusReviewImage('seal');
  await h.$('#review-browse-next').emit('click');
  assert.equal(h.state.review.current.id, 43);
  assert.equal(h.state.review.reviewImages[h.state.review.reviewImageIndex].group, 'seal');
});

// Use both real controllers so these cases cover the two native dialog layers,
// including hashchange listeners registered before navigation in the real app.
async function statisticsReviewHarness({open = true} = {}) {
  const h = harness({hash: '#samples'});
  h.navigation.showPage('samples'); h.review.initialize();
  const statisticsDialog = h.$('#sample-statistics-dialog'); statisticsDialog.tagName = 'DIALOG';
  const body = h.$('#sample-statistics-body'), table = h.$('.sample-stat-table-wrap');
  h.document.body.append(statisticsDialog); statisticsDialog.append(body);
  body.querySelector = selector => selector === '.sample-stat-table-wrap' ? table : null;
  const cell = state => ({state, decision: '匹配', value: '客户章'});
  const items = Array.from({length: 8}, (_, index) => ({
    filename: `sample${index + 41}.jpg`, result_id: index + 41, company: '甲公司', requirement: '客户章',
    expected_seal: '匹配', expected_date: '2026-01-01', job_status: 'succeeded',
    date: cell('正确'), seals: {qingtong_template: cell('错误')},
  }));
  const data = {items, columns: [{key: 'qingtong_template', label: '清瞳 · 印章模板'}], batches: []};
  const statisticsRequests = [];
  const statistics = createSampleStatistics({environment: h.environment, ui: h.ui,
    services: {samples: {statistics: async task => { statisticsRequests.push(task); return data; }}},
    openResult: (id, options) => h.review.openReview(id, {sample: true, ...options}),
  });
  statistics.initialize(); h.navigation.initialize();
  await statistics.show({company: '甲公司', companies: [{name: '甲公司'}]});
  await body.emit('click', {target: {closest: selector => selector === '[data-stat-drill]'
    ? {dataset: {statDrill: 'wrong', statChannel: 'qingtong_template', statRequirement: '客户章'}} : null}});
  await h.$('#sample-statistics-next').emit('click');
  body.scrollTop = 215; table.scrollTop = 85; table.scrollLeft = 330;
  const source = element(h.document, 'BUTTON'); source.dataset.statView = 'sample47.jpg';
  source.closest = selector => selector === '[data-stat-view]' ? source : null;
  body.append(source); source.focus();
  h.behavior.api = async route => {
    const id = Number(route.match(/results\/(\d+)/)[1]);
    return {id, filename: `sample${id}.jpg`, fields: {}};
  };
  const openSource = () => body.emit('click', {target: source});
  const snapshot = {markup: body.innerHTML, pagination: h.$('#sample-statistics-page').textContent};
  if (open) await openSource();
  return {...h, statistics, statisticsDialog, statisticsRequests, body, statisticsTable: table, source, openSource, snapshot};
}

const settleDialogs = () => new Promise(resolve => setImmediate(resolve));

function assertStatisticsContext(h) {
  assert.equal(h.statisticsDialog.open, true);
  assert.equal(h.statisticsDialog.showCount, 1);
  assert.equal(h.statisticsDialog.dataset.section, 'details');
  assert.equal(h.$('#sample-statistics-company').value, '甲公司');
  assert.equal(h.$('#sample-statistics-page').textContent, h.snapshot.pagination);
  assert.equal(h.body.innerHTML, h.snapshot.markup);
  assert.equal(h.body.scrollTop, 215);
  assert.equal(h.statisticsTable.scrollTop, 85);
  assert.equal(h.statisticsTable.scrollLeft, 330);
}

for (const method of ['close', 'escape', 'back']) {
  test(`statistics ${method} returns to the same filtered page, scroll and source focus after sample arrows`, async () => {
    const h = await statisticsReviewHarness();
    assertStatisticsContext(h);
    assert.equal(h.dialog.open, true);
    assert.equal(h.$('#review-dialog .review-dialog-close span').textContent, '返回统计');
    assert.equal(h.$('#review-dialog .review-dialog-close').getAttribute('aria-label'), '关闭查看样单，返回统计明细');
    assert.equal(h.$('#review-browse-position').textContent, '7 / 8');
    await h.$('#review-browse-next').emit('click');
    assert.equal(h.state.review.current.id, 48);
    assert.equal(h.$('#review-browse-position').textContent, '8 / 8');
    await h.statistics.refresh();
    assertStatisticsContext(h);
    h.$('#review-close').focus();
    if (method === 'close') h.review.closeModal();
    else if (method === 'escape') await h.dialog.emit('cancel');
    else { h.location.hash = '#samples'; await h.window.emit('hashchange'); }
    await settleDialogs();
    assert.equal(h.dialog.open, false);
    assert.equal(h.location.hash, '#samples');
    assert.equal(h.state.review.current, null);
    assert.equal(h.document.activeElement, h.source);
    assertStatisticsContext(h);
  });
}

test('cancelled unsaved browser Back keeps both the sample and its statistics context even outside samples', async () => {
  const h = await statisticsReviewHarness();
  h.state.review.reviewDirty = true;
  h.location.hash = '#records'; await h.window.emit('hashchange'); await settleDialogs();
  assert.equal(h.location.hash, '#sample-view/47');
  assert.equal(h.dialog.open, true);
  assert.equal(h.state.review.reviewDirty, true);
  assertStatisticsContext(h);
  h.window.confirm = () => true;
  h.location.hash = '#records'; await h.window.emit('hashchange'); await settleDialogs();
  assert.equal(h.dialog.open, false);
  assert.equal(h.statisticsDialog.open, false);
  assert.equal(h.location.hash, '#records');
  assert.equal(h.statisticsDialog.showCount, 1);
});

test('statistics stays usable when a sample cannot load, and a closed pending preview cannot reopen it', async () => {
  const h = await statisticsReviewHarness({open: false});
  h.behavior.api = async () => { throw Error('样单暂时无法读取'); };
  await h.openSource(); await settleDialogs();
  assert.equal(h.dialog.open, false);
  assert.equal(h.location.hash, '#samples');
  assert.match(h.$('#sample-statistics-feedback').textContent, /样单暂时无法读取/);
  assertStatisticsContext(h);
  let resolve;
  h.behavior.api = () => new Promise(done => { resolve = done; });
  const opening = h.openSource();
  h.location.hash = '#records'; await h.window.emit('hashchange');
  resolve({id: 47, filename: 'sample47.jpg', fields: {}}); await opening; await settleDialogs();
  assert.equal(h.dialog.open, false);
  assert.equal(h.statisticsDialog.open, false);
  assert.equal(h.statisticsDialog.showCount, 1);
  assert.equal(h.location.hash, '#records');
});

test('closing statistics during a slow sample request cancels it even though the samples route has not changed', async () => {
  const h = await statisticsReviewHarness({open: false});
  let resolve;
  h.behavior.api = () => new Promise(done => { resolve = done; });
  const opening = h.openSource();
  h.statisticsDialog.close(); await settleDialogs();
  assert.equal(h.location.hash, '#samples');
  resolve({id: 47, filename: 'sample47.jpg', fields: {}}); await opening; await settleDialogs();
  assert.equal(h.dialog.open, false);
  assert.equal(h.state.review.current, null);
  assert.equal(h.statisticsDialog.open, false);
  assert.equal(h.location.hash, '#samples');
});

test('ordinary sample viewing keeps the close label after leaving statistics', async () => {
  const h = await statisticsReviewHarness();
  h.review.closeModal(); await settleDialogs();
  h.statisticsDialog.close(); await settleDialogs();
  await h.review.openReview(47, {sample: true});
  assert.equal(h.$('#review-dialog .review-dialog-close span').textContent, '关闭');
  assert.equal(h.$('#review-dialog .review-dialog-close').getAttribute('aria-label'), '关闭查看样单，返回样单中心');
});

test('returning to statistics does not wait for ancillary review history and its late response cannot consume a new preview', async () => {
  const h = await statisticsReviewHarness({open: false});
  let resolveHistory;
  h.behavior.renderHistory = () => new Promise(done => { resolveHistory = done; });
  const opening = h.openSource(); await settle();
  assert.equal(h.dialog.open, true);
  const requestsBefore = h.statisticsRequests.length;
  await h.statistics.refresh(); // Deferred until returning to statistics.
  assert.equal(h.statisticsRequests.length, requestsBefore);
  h.review.closeModal(); await settleDialogs();
  assert.equal(h.dialog.open, false);
  assert.equal(h.statisticsRequests.length, requestsBefore + 1);
  assertStatisticsContext(h);
  h.behavior.renderHistory = async () => {};
  await h.openSource();
  assert.equal(h.dialog.open, true);
  resolveHistory(); await opening;
  assert.equal(h.dialog.open, true);
  assert.equal(h.state.review.current.id, 47);
  assertStatisticsContext(h);
});

function installCallDetails(h) {
  const filename = h.state.review.current.filename, getResult = h.behavior.api, calls = [];
  h.behavior.api = async route => {
    calls.push(route);
    if (route.endsWith('/recognition-calls')) return {
      filename, available: true, status: 'succeeded',
      summary: {total: 2, succeeded: 2, failed: 0, cache_hits: 1},
      processing_seconds: 3,
      calls: [1, 2].map(id => ({id, stage: 'seal', stage_label: '客户印章', method: 'paddle_v6',
        method_label: 'Paddle', action: '文字识别', input_name: `seal-part-${id}.png`,
        seconds: id, status: 'succeeded'})),
    };
    return getResult(route);
  };
  const click = selector => {
    const target = element(h.document, 'BUTTON');
    target.closest = candidate => candidate === selector ? target : null;
    return h.$('#review-runs-panel').emit('click', {target});
  };
  return {calls, click};
}

test('physical call details leave sample browsing position intact and switch with the next receipt', async () => {
  const h = await sampleBrowseHarness(), details = installCallDetails(h);
  await h.$('#review-runs-open').emit('click');
  assert.match(h.$('#review-runs-body').innerHTML, /seal-part-1.png/);
  assert.equal(h.$('#review-browse-position').textContent, '2 / 3');
  await h.$('#review-browse-next').emit('click');
  assert.equal(h.state.review.current.id, 43);
  assert.equal(h.$('#review-runs-panel').hidden, true);
  assert.equal(h.$('#review-form').inert, false);
  assert.equal(h.$('#review-browse-position').textContent, '3 / 3');
  await h.$('#review-browse-prev').emit('click');
  assert.equal(h.state.review.current.id, 42);
  assert.equal(h.dialog.showCount, 1);
  assert.deepEqual(details.calls.filter(route => route.endsWith('/recognition-calls')),
    ['/api/samples/results/42/recognition-calls']);
});

test('closing call details invalidates a pending response and preserves unsaved edits and scroll', async () => {
  const h = await sampleBrowseHarness(), details = installCallDetails(h);
  h.review.setReviewDirty();
  const form = h.$('#review-form'); form.scrollTop = 230; form.value = 'existing draft';
  let resolve;
  h.behavior.api = () => new Promise(done => { resolve = done; });
  const opening = h.$('#review-runs-open').emit('click');
  await details.click('[data-runs-close]');
  resolve({filename:'42.jpg', available:true, summary:{total:99}, calls:[]}); await opening;
  assert.equal(h.state.review.current.id, 42);
  assert.equal(h.state.review.reviewDirty, true);
  assert.equal(h.$('#review-runs-panel').hidden, true);
  assert.equal(form.inert, false); assert.equal(form.scrollTop, 230);
  assert.equal(form.value, 'existing draft');
  assert.doesNotMatch(h.$('#review-runs-body').innerHTML, />99</);
  assert.equal(h.document.activeElement, h.$('#review-runs-open'));
});

test('returning from physical call details retains the original statistics filters page and focus', async () => {
  const h = await statisticsReviewHarness(); installCallDetails(h);
  await h.$('#review-runs-open').emit('click');
  assert.equal(h.state.review.current.id, 47);
  assert.equal(h.$('#review-dialog .review-dialog-close span').textContent, '返回统计');
  assertStatisticsContext(h);
  h.review.closeModal(); await settleDialogs();
  assert.equal(h.dialog.open, false);
  assert.equal(h.location.hash, '#samples');
  assert.equal(h.document.activeElement, h.source);
  assertStatisticsContext(h);
});
