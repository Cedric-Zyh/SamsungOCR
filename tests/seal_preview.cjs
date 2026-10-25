const assert = require('node:assert/strict');
const test = require('node:test');
const {standardDetail} = require('../static/modules/seals/standard_view.mjs');
const {bindStandardPreview} = require('../static/modules/seals/standard_preview.mjs');

const standard = () => ({
  id: 12,
  requirement: '深圳欧瑞特供应链管理有限公司兰州分公司收货专用章（1）',
  recognized: '深圳欧瑞特供应链管理有限公司\n兰州分公司 收货专用章（1）',
  shape: 'round', color: 'red', image_url: '/stamp.png', revision: 3,
  source_result_id: 45, source_filename: '兰州回单.jpg', source_kind: 'receipt',
  updated_at: '2026-10-23T18:35:10',
});

test('detail keeps the branch and stamp number visible and separates reviewed requirements from OCR', () => {
  const markup = standardDetail(standard());
  assert.match(markup, /<h2>深圳欧瑞特供应链管理有限公司 · 兰州分公司<\/h2>/);
  assert.match(markup, /seal-detail-subtitle">收货专用章（1）/);
  assert.match(markup, /<dt>签章要求<\/dt><dd>深圳欧瑞特供应链管理有限公司兰州分公司收货专用章（1）/);
  assert.match(markup, /<dt>识别文字<\/dt><dd>深圳欧瑞特供应链管理有限公司\n兰州分公司 收货专用章（1）/);
  assert.match(markup, /供人工核对/);
  assert.match(markup, /当前生效/);
  assert.match(markup, /第 3 版/);
  assert.match(markup, /更新于 2026-10-23 18:35/);
  assert.equal((markup.match(/data-load-standard-sources/g) || []).length, 1);
  assert.ok(markup.indexOf('data-load-standard-sources') < markup.indexOf('seal-detail-preview'));
});

test('receipt and sample sources have distinct navigation metadata, while bootstrap or missing sources remain text', () => {
  assert.match(standardDetail(standard()), /data-standard-source="45" data-source-kind="receipt"/);
  const sample = standardDetail({...standard(), source_kind:'sample'});
  assert.match(sample, /data-standard-source="45" data-source-kind="sample"/);
  assert.match(sample, /<dt>来源样单<\/dt>/);
  for (const source of [{source_result_id:0}, {source_result_id:null}, {source_kind:'bootstrap'}]) {
    const markup = standardDetail({...standard(), ...source});
    assert.doesNotMatch(markup, /data-standard-source=/);
    assert.match(markup, /兰州回单.jpg/);
  }
});

test('detail escapes all text and attribute content, including source filenames and OCR', () => {
  const markup = standardDetail({...standard(), requirement:'要求 <script>bad</script>',
    recognized:'<img src=x onerror=bad()>', source_filename:'\" onclick=\"bad()',
    image_url:'/stamp.png?name=\"bad&other=<test>', revision:'<b>3</b>'});
  assert.doesNotMatch(markup, /<script>|<img src=x|<b>3<\/b>|title="" onclick=/);
  assert.match(markup, /&lt;script&gt;bad&lt;\/script&gt;/);
  assert.match(markup, /&lt;img src=x onerror=bad\(\)&gt;/);
  assert.match(markup, /&quot; onclick=&quot;bad\(\)/);
  assert.match(markup, /\/stamp.png\?name=&quot;bad&amp;other=&lt;test&gt;/);
});

function previewHarness() {
  const controls = ['fit', 'out', 'in'].map(action => {
    const listeners = new Map();
    return {
      dataset: {sealZoom: action}, disabled: false,
      addEventListener: (name, listener) => listeners.set(name, listener),
      removeEventListener: (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); },
      click() { listeners.get('click')?.(); },
    };
  });
  const viewport = {clientWidth:600, clientHeight:280, scrollLeft:0, scrollTop:0};
  const image = {style:{}};
  const label = {textContent:''};
  let resized, observing = false;
  const detail = {
    ownerDocument: {defaultView: {ResizeObserver: class {
      constructor(callback) { resized = callback; }
      observe(node) { assert.equal(node, viewport); observing = true; }
      disconnect() { observing = false; }
    }}},
    querySelector: selector => ({'.seal-preview-viewport':viewport, '.seal-preview-image':image, '[data-seal-zoom-label]':label}[selector]),
    querySelectorAll: () => controls,
  };
  const cleanup = bindStandardPreview(detail);
  const [fit, out, zoomIn] = controls;
  return {viewport, image, label, fit, out, zoomIn, cleanup, resize: () => resized(), observing: () => observing};
}

test('zoom stays inside the pane, preserves the center, and fit restores the complete image', () => {
  const h = previewHarness();
  assert.equal(h.out.disabled, true);
  assert.equal(h.image.style.width, '100%');
  h.zoomIn.click();
  assert.equal(h.label.textContent, '125%');
  assert.equal(h.image.style.width, '750px');
  assert.equal(h.image.style.height, '350px');
  assert.equal(h.viewport.scrollLeft, 75);
  assert.equal(h.viewport.scrollTop, 35);
  h.fit.click();
  assert.equal(h.label.textContent, '100%');
  assert.equal(h.image.style.width, '100%');
  assert.equal(h.image.style.height, '100%');
  assert.equal(h.viewport.scrollLeft, 0);
  assert.equal(h.viewport.scrollTop, 0);
  assert.equal(h.out.disabled, true);
});

test('zoom has an upper bound, responds to resizing, and stops observing after detail navigation', () => {
  const h = previewHarness();
  for (let i = 0; i < 20; i++) h.zoomIn.click();
  assert.equal(h.label.textContent, '400%');
  assert.equal(h.zoomIn.disabled, true);
  h.viewport.clientWidth = 500;
  h.viewport.clientHeight = 240;
  h.resize();
  assert.equal(h.image.style.width, '2000px');
  assert.equal(h.image.style.height, '960px');
  h.out.click();
  assert.equal(h.zoomIn.disabled, false);
  assert.equal(h.observing(), true);
  h.cleanup();
  assert.equal(h.observing(), false);
  const lastLabel = h.label.textContent;
  h.fit.click();
  assert.equal(h.label.textContent, lastLabel);
});
