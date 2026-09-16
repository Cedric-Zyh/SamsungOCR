const assert = require('node:assert/strict');
const test = require('node:test');
const {createReviewPreview} = require('../static/modules/review_preview.mjs');
const {createReviewImage} = require('../static/modules/review_image.mjs');

function element() {
  const listeners = new Map(), classes = new Set(), captures = new Set();
  return {
    src: '', disabled: false, textContent: '', dataset: {}, attributes: {}, style: {},
    classList: {contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name)},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; if (name === 'src') this.src = ''; },
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); },
    dispatch(type, details = {}) {
      for (const callback of listeners.get(type) || []) callback({target: this, button: 0, pointerId: 1, preventDefault() {}, ...details});
    },
    setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id), releasePointerCapture: id => captures.delete(id),
    scrollLeft: 100, scrollTop: 100, scrollTo(left, top) { this.scrollLeft = left; this.scrollTop = top; },
  };
}

function harness() {
  const nodes = new Map(), loaders = [], controls = [element(), element()];
  const $ = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const ui = {$, $$: selector => selector === '[data-zoom]' ? controls : []};
  const controller = createReviewPreview({environment: {}, ui, createImage: () => {
    const loader = {...element(), naturalWidth: 800}; loaders.push(loader); return loader;
  }});
  let retries = 0, fallbacks = 0;
  const attach = () => controller.attach({}, {retry: () => retries++, fallback: () => fallbacks++});
  attach();
  return {controller, $, controls, loaders, attach, counts: () => ({retries, fallbacks})};
}

const page = {url: '/files/page.jpg', label: '整页回单', group: 'page'};
const date = {url: '/files/date.jpg', label: '日期区域 1', group: 'date'};
const seal = {url: '/files/seal.jpg', label: '印章区域 1', group: 'seal'};

test('switching regions hides the old image and blocks zoom until the requested image is loaded', () => {
  const h = harness(), image = h.$('[data-preview]'), viewport = h.$('[data-image-viewport]');
  h.controller.show(page, {alt: 'A · 整页回单'});
  h.loaders[0].onload();
  assert.equal(image.src, page.url);
  assert.equal(image.classList.contains('hidden'), false);
  h.controller.show(date, {alt: 'A · 日期区域 1', caption: '当前核对日期'});
  assert.equal(image.src, '');
  assert.equal(image.classList.contains('hidden'), true);
  assert.equal(viewport.attributes['aria-busy'], 'true');
  assert.match(h.$('[data-image-message]').textContent, /正在加载日期/);
  assert(h.controls.every(button => button.disabled));
  h.loaders[1].onload();
  assert.equal(image.src, date.url);
  assert.equal(image.alt, 'A · 日期区域 1');
  assert.equal(viewport.dataset.imageState, 'ready');
  assert.equal(viewport.attributes['aria-busy'], 'false');
  assert(h.controls.every(button => !button.disabled));
  assert.equal(h.$('[data-image-caption]').textContent, '当前核对日期');
});

test('failed region provides retry and full-page recovery without exposing broken imagery', () => {
  const h = harness();
  h.controller.show(date, {canFallback: true});
  h.loaders[0].onerror();
  assert.equal(h.$('[data-image-viewport]').dataset.imageState, 'error');
  assert.equal(h.$('[data-preview]').classList.contains('hidden'), true);
  assert.equal(h.$('[data-image-retry]').classList.contains('hidden'), false);
  assert.equal(h.$('[data-image-fallback]').classList.contains('hidden'), false);
  h.$('[data-image-retry]').dispatch('click'); h.$('[data-image-fallback]').dispatch('click');
  assert.deepEqual(h.counts(), {retries: 1, fallbacks: 1});
  h.controller.show(date, {retry: true, canFallback: true});
  assert.match(h.loaders[1].src, /^\/files\/date\.jpg\?_preview_retry=/);
  h.loaders[1].onload();
  assert.equal(h.$('[data-image-viewport]').dataset.imageState, 'ready');
  assert.equal(h.$('[data-image-empty]').classList.contains('hidden'), true);
});

test('out-of-order load and error events never replace the currently requested region', () => {
  const h = harness();
  h.controller.show(page);
  const oldLoad = h.loaders[0].onload, oldError = h.loaders[0].onerror;
  h.controller.show(date);
  const dateLoad = h.loaders[1].onload, dateError = h.loaders[1].onerror;
  oldLoad(); oldError();
  assert.equal(h.$('[data-image-viewport]').dataset.imageState, 'loading');
  h.controller.show(seal);
  h.loaders[2].onload();
  dateError(); dateLoad(); oldLoad();
  assert.equal(h.$('[data-preview]').src, seal.url);
  assert.equal(h.$('[data-image-viewport]').dataset.imageState, 'ready');
});

test('leaving or replacing the review cancels pending image feedback', () => {
  const h = harness();
  h.controller.show(page);
  const stale = h.loaders[0].onload;
  h.controller.cancel();
  stale();
  assert.equal(h.$('[data-preview]').src, '');
  h.attach();
  h.controller.show(date);
  h.loaders[1].onload();
  stale();
  assert.equal(h.$('[data-preview]').src, date.url);
});

test('records without previews and failed full-page images offer only available actions', () => {
  const h = harness();
  h.controller.show(null);
  assert.equal(h.loaders.length, 0);
  assert.equal(h.$('[data-image-viewport]').dataset.imageState, 'empty');
  assert.equal(h.$('[data-image-retry]').classList.contains('hidden'), true);
  h.controller.show(page);
  h.loaders[0].naturalWidth = 0;
  h.loaders[0].onload();
  assert.equal(h.$('[data-image-viewport]').dataset.imageState, 'error');
  assert.equal(h.$('[data-image-fallback]').classList.contains('hidden'), true);
});

test('changing the preview resets and releases an active image drag', () => {
  const nodes = new Map();
  const $ = selector => {
    if (selector === '[data-review-divider]') return null;
    if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector);
  };
  const controller = createReviewImage({environment: {}, ui: {$, $$: () => []}, reviewState: {reviewZoom: 1}});
  controller.attach({});
  const viewport = $('[data-image-viewport]');
  viewport.dispatch('pointerdown', {clientX: 100, clientY: 100});
  assert.equal(viewport.hasPointerCapture(1), true);
  controller.reset();
  viewport.dispatch('pointermove', {clientX: 20, clientY: 20});
  assert.equal(viewport.hasPointerCapture(1), false);
  assert.equal(viewport.classList.contains('is-dragging'), false);
  assert.equal(viewport.scrollLeft, 0);
});
