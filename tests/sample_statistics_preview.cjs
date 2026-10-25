const assert = require('node:assert/strict');
const test = require('node:test');
const {createStatisticsPreview} = require('../static/modules/samples/statistics_preview.mjs');

function harness() {
  const pendingEvents = [], nodes = new Map(), returned = [], results = [], originals = [];
  const document = {activeElement: null};
  function node() {
    const listeners = new Map();
    return {
      open: false, isConnected: true, scrollTop: 0, scrollLeft: 0, textContent: '', showCount: 0,
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
      },
      emit(type) { for (const fn of listeners.get(type) || []) fn({target: this}); },
      showModal() { this.open = true; this.showCount++; },
      close() { if (!this.open) return; this.open = false; pendingEvents.push(() => this.emit('close')); },
      focus() { document.activeElement = this; },
    };
  }
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  };
  const location = {hash: '#samples'}, window = node();
  const statistics = $('#sample-statistics-dialog'), review = $('#review-dialog'), original = $('#original-preview-dialog');
  const body = $('#sample-statistics-body'), table = $('.sample-stat-table-wrap'), source = $('[source]');
  body.querySelector = selector => selector === '.sample-stat-table-wrap' ? table : null;
  statistics.showModal(); body.scrollTop = 210; table.scrollTop = 40; table.scrollLeft = 160;
  source.focus();
  const behavior = {
    openResult: async (_id, options) => { if (options.isCurrent()) review.showModal(); },
    openOriginal: async () => original.showModal(),
  };
  const preview = createStatisticsPreview({environment: {document, window, location}, ui: {$},
    openResult: (...args) => { results.push(args); return behavior.openResult(...args); },
    openOriginal: (...args) => { originals.push(args); return behavior.openOriginal(...args); },
    imageUrl: filename => `/samples/${filename}`, onReturn: () => returned.push(location.hash),
  });
  preview.initialize();
  const flush = () => { for (const fn of pendingEvents.splice(0)) fn(); };
  return {$, preview, statistics, review, original, body, table, source, behavior, returned, results, originals,
    window, location, document, flush};
}

test('sample preview preserves both dialogs and restores position and focus exactly once', async () => {
  const h = harness(), ids = [9, 4, 11];
  await h.preview.open({result_id: 4, filename: 'four.jpg'}, ids, h.source);
  assert.equal(h.statistics.open, true); assert.equal(h.review.open, true);
  assert.equal(h.preview.active(), true);
  assert.deepEqual(h.results[0][1].browseIds, ids);
  ids.push(12); assert.deepEqual(h.results[0][1].browseIds, [9, 4, 11]);
  h.body.scrollTop = 0; h.table.scrollTop = 0; h.table.scrollLeft = 0;
  h.$('[review-close]').focus();
  h.review.close(); assert.equal(h.preview.active(), true); h.flush();
  assert.equal(h.preview.active(), false);
  assert.equal(h.body.scrollTop, 210); assert.equal(h.table.scrollTop, 40); assert.equal(h.table.scrollLeft, 160);
  assert.equal(h.document.activeElement, h.source); assert.deepEqual(h.returned, ['#samples']);
  assert.equal(h.statistics.showCount, 1);
  h.review.emit('close'); assert.equal(h.returned.length, 1);
});

test('original-only samples use their source button and return without closing statistics', async () => {
  const h = harness();
  await h.preview.open({filename: 'untested.jpg'}, [], h.source);
  assert.deepEqual(h.originals, [[{filename: 'untested.jpg', sample_center: true,
    preview_url: '/samples/untested.jpg'}, h.source]]);
  assert.equal(h.results.length, 0);
  assert.equal(h.original.open, true); assert.equal(h.statistics.open, true);
  h.original.close(); h.flush();
  assert.equal(h.document.activeElement, h.source);
  assert.equal(h.preview.active(), false); assert.equal(h.returned.length, 1);
});

test('failed or declined openings keep statistics usable and expose loading errors there', async () => {
  const h = harness();
  h.behavior.openResult = async () => {};
  await h.preview.open({result_id: 4}, [4], h.source);
  assert.equal(h.preview.active(), false); assert.equal(h.statistics.open, true);
  h.behavior.openResult = async () => { throw Error('暂时无法读取'); };
  await h.preview.open({result_id: 4}, [4], h.source);
  assert.equal(h.preview.active(), false); assert.equal(h.statistics.open, true);
  assert.match(h.$('#sample-statistics-feedback').textContent, /暂时无法读取/);
  assert.equal(h.document.activeElement, h.source);
});

test('hash handling waits for navigation to accept or reject leaving an unsaved sample', async () => {
  const h = harness();
  await h.preview.open({result_id: 4}, [4], h.source);
  h.location.hash = '#records'; h.window.emit('hashchange');
  // The later navigation handler rejected leaving, just as with unsaved edits.
  h.location.hash = '#sample-view/4'; await Promise.resolve();
  assert.equal(h.statistics.open, true); assert.equal(h.preview.active(), true);
  h.location.hash = '#records'; h.window.emit('hashchange'); await Promise.resolve();
  assert.equal(h.statistics.open, false); assert.equal(h.preview.active(), false);
  h.review.close(); h.flush();
  assert.equal(h.statistics.showCount, 1); assert.equal(h.returned.length, 0);
});

test('a late close from a previous preview cannot consume a newer opening', async () => {
  const h = harness();
  await h.preview.open({result_id: 4}, [4, 5], h.source);
  h.review.close(); h.preview.clear();
  let resolve;
  h.behavior.openResult = (_id, options) => new Promise(done => {
    resolve = () => { if (options.isCurrent()) h.review.showModal(); done(); };
  });
  const opening = h.preview.open({result_id: 5}, [4, 5], h.source);
  h.flush(); assert.equal(h.preview.active(), true);
  resolve(); await opening;
  h.review.emit('close'); assert.equal(h.preview.active(), true);
  h.review.close(); h.flush(); assert.equal(h.preview.active(), false);
  assert.equal(h.returned.length, 1);
});

test('closing statistics invalidates a pending sample read without requiring a route change', async () => {
  const h = harness();
  let resolve, canOpen;
  h.behavior.openResult = (_id, options) => {
    canOpen = options.isCurrent;
    return new Promise(done => { resolve = () => { if (canOpen()) h.review.showModal(); done(); }; });
  };
  const opening = h.preview.open({result_id: 4}, [4], h.source);
  assert.equal(canOpen(), true);
  h.statistics.close(); assert.equal(canOpen(), false); h.flush();
  resolve(); await opening;
  assert.equal(h.location.hash, '#samples'); assert.equal(h.review.open, false);
  assert.equal(h.preview.active(), false); assert.equal(h.returned.length, 0);
});

test('a disconnected source falls back to the statistics details tab', async () => {
  const h = harness();
  await h.preview.open({result_id: 4}, [4], h.source);
  h.source.isConnected = false; h.review.close(); h.flush();
  assert.equal(h.document.activeElement, h.$('#sample-statistics-details-tab'));
});
