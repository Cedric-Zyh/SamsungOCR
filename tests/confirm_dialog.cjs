const assert = require('node:assert/strict');
const test = require('node:test');
const {confirmInline} = require('../static/modules/inline_confirm.mjs');

function fixture() {
  const nodes = new Map();
  let focused, panel;
  const element = tag => ({
    tag, attributes: {}, listeners: {}, isConnected: true,
    setAttribute(key, value) { this.attributes[key] = value; },
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, element(selector));
      return nodes.get(selector);
    },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    emit(type, event = {}) { this.listeners[type]?.(event); },
    focus() { focused = this; },
    showModal() { this.open = true; this.modal = true; },
    close() { this.open = false; this.emit('close'); },
    remove() { this.isConnected = false; },
  });
  const anchor = element('button');
  const environment = {document: {
    activeElement: anchor,
    createElement: tag => (panel = element(tag)),
    body: {append() {}},
  }, window: {confirm() { throw new Error('Should use the dialog'); }}};
  return {environment, anchor, nodes, panel: () => panel, focus: () => focused};
}

test('confirmation opens a modal dialog with title, message and safe initial focus', async () => {
  const f = fixture();
  const pending = confirmInline({...f, message: '<回单>上传提示', confirmLabel: '继续上传'});
  const panel = f.panel();
  assert.equal(panel.tag, 'dialog');
  assert.equal(panel.modal, true);
  assert.equal(panel.attributes['aria-modal'], 'true');
  assert.equal(panel.attributes['aria-labelledby'], 'retry-confirm-title');
  assert.equal(f.nodes.get('h2').textContent, '确认重新识别');
  assert.equal(f.nodes.get('p').textContent, '<回单>上传提示');
  assert.equal(f.nodes.get('[data-inline-confirm-ok]').textContent, '继续上传');
  assert.equal(f.focus(), f.nodes.get('[data-inline-confirm-cancel]'));
  f.nodes.get('[data-inline-confirm-ok]').emit('click');
  assert.equal(await pending, true);
  assert.equal(panel.open, false);
  assert.equal(panel.isConnected, false);
  assert.equal(f.focus(), f.anchor);
});

for (const action of ['cancel-button', 'escape', 'close']) {
  test(`${action} dismisses without authorizing a retry`, async () => {
    const f = fixture();
    const pending = confirmInline({...f, message: '上传确认'});
    if (action === 'cancel-button') f.nodes.get('[data-inline-confirm-cancel]').emit('click');
    else if (action === 'escape') {
      let prevented = false;
      f.panel().emit('cancel', {preventDefault() { prevented = true; }});
      assert.equal(prevented, true);
    } else f.panel().close();
    assert.equal(await pending, false);
    assert.equal(f.panel().isConnected, false);
    assert.equal(f.focus(), f.anchor);
  });
}

test('modal keyboard events cannot reach review save or approve shortcuts', async () => {
  const f = fixture();
  const pending = confirmInline({...f, message: '上传确认'});
  for (const key of ['Enter', 's', 'Escape', 'Tab']) {
    let stopped = false;
    f.panel().emit('keydown', {key, stopPropagation() { stopped = true; }});
    assert.equal(stopped, true);
  }
  f.panel().close();
  assert.equal(await pending, false);
});

test('non-DOM fallback never authorizes a retry without confirmation', async () => {
  assert.equal(await confirmInline({environment: {}, message: '上传确认'}), false);
  assert.equal(await confirmInline({environment: {window: {confirm: () => false}}, message: '上传确认'}), false);
  assert.equal(await confirmInline({environment: {window: {confirm: () => true}}, message: '上传确认'}), true);
});
