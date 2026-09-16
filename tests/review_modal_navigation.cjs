const assert = require('node:assert/strict');
const test = require('node:test');
const {createNavigation} = require('../static/modules/navigation.mjs');
const {createReview} = require('../static/modules/review.mjs');
const {createReviewQueue} = require('../static/modules/review_queue.mjs');
const {createState} = require('../static/modules/state.mjs');

// Production controllers share their real state; only browser surfaces and I/O
// are replaced so these tests exercise close/navigation races without storage.
function element(document, tagName = 'DIV') {
  const classes = new Set(), listeners = new Map();
  return {
    tagName, dataset: {}, attributes: {}, children: [], parentNode: null, parentElement: null,
    value: '', textContent: '', innerHTML: '', disabled: false, open: false, isConnected: true,
    scrollTop: 0, scrollLeft: 0, style: {}, showCount: 0, closeCount: 0,
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
    contains(node) { return node === this || this.children.some(child => child.contains(node)); },
    focus() { document.activeElement = this; },
    closest(selector) {
      for (let node = this; node; node = node.parentElement) {
        if (selector === '[data-open-review]' && node.dataset.openReview) return node;
        if (selector === '[data-page="records"]' && node.dataset.page === 'records') return node;
      }
      return null;
    },
    showModal() { assert.equal(this.open, false); this.open = true; this.showCount++; },
    close() { this.open = false; this.closeCount++; },
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    async emit(name, props = {}) {
      const event = {target: this, currentTarget: this, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...props};
      for (const callback of listeners.get(name) || []) await callback(event);
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
  const pages = ['progress', 'records', 'quality', 'settings', 'review'].map(page => {
    const node = $(`[data-page="${page}"]`); node.dataset.page = page; return node;
  });
  const links = ['progress', 'records', 'quality', 'settings'].map(page => {
    const node = $(`[data-nav="${page}"]`); node.dataset.nav = page; return node;
  });
  const dialog = $('#review-dialog'); dialog.tagName = 'DIALOG';
  document.body.append(...pages.filter(node => node.dataset.page !== 'review'), $('#toast'), dialog);
  dialog.append($('#review-close'), $('[data-page="review"]'));
  const opener = $('[data-open-review="41"]'); opener.dataset.openReview = '41';
  $('[data-page="records"]').append(opener, $('#record-search')); document.activeElement = opener;
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
  const behavior = {api: async () => { throw Error('Unexpected request'); }, loadReviewQueue: async () => {}};
  let navigation;
  const review = createReview({environment, ui, navigationState: state.navigation, reviewState: state.review,
    api: (...args) => behavior.api(...args), ReceiptWorkbench: {}, fieldSchema: {},
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
  };
  navigation = createNavigation(options);
  return {document, window, location, historyCalls, nodes, $, dialog, opener, table,
    state, review, navigation, options, loads, notices, opened, environment, ui, behavior};
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

test('review opens above the records page, keeps the records navigation selected, and mounts feedback inside the dialog', () => {
  const h = harness(); h.state.review.reviewOrigin = {hash: '#records', scroll: 75};
  h.navigation.showPage('review');
  assert.equal(h.dialog.open, true);
  assert.equal(h.document.body.dataset.activePage, 'review');
  assert.equal(h.$('[data-page="records"]').classList.contains('hidden'), false);
  assert.equal(h.$('[data-page="review"]').classList.contains('hidden'), false);
  assert.equal(h.$('[data-page="progress"]').classList.contains('hidden'), true);
  assert.equal(h.$('[data-nav="records"]').getAttribute('aria-current'), 'page');
  assert.equal(h.$('#page-title').textContent, '回单记录');
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
