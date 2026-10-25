const assert = require('node:assert/strict');
const test = require('node:test');
const {initializeSettingsTabs} = require('../static/modules/settings/tabs.mjs');

const keys = ['recognition', 'rules', 'services', 'data'];

function node(dataset = {}) {
  const listeners = new Map();
  return {
    dataset, attributes: {}, hidden: false, focusCount: 0,
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(type, callback) {
      const callbacks = listeners.get(type) || [];
      callbacks.push(callback);
      listeners.set(type, callbacks);
    },
    focus() { this.focusCount += 1; },
    fire(type, detail = {}) {
      const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...detail};
      listeners.get(type)?.forEach(callback => callback(event));
      return event;
    },
    listenerCount(type) { return listeners.get(type)?.length || 0; },
  };
}

function harness({defaultView} = {}) {
  const tabs = keys.map(key => node({settingsTab: key}));
  const panels = keys.map(key => node({settingsPanel: key}));
  const savePanel = node();
  const tablist = {querySelectorAll: selector => {
    assert.equal(selector, 'button.settings-tab[data-settings-tab]');
    return tabs;
  }};
  const page = {
    querySelector: selector => ({'.settings-tabs[role="tablist"]': tablist, '.settings-auto-save-panel': savePanel})[selector] || null,
    querySelectorAll: selector => {
      assert.equal(selector, '.settings-tab-panel[data-settings-panel]');
      return panels;
    },
  };
  const document = {defaultView, querySelector: selector => {
    assert.equal(selector, 'section[data-page="settings"]');
    return page;
  }};
  const controller = initializeSettingsTabs(document);
  return {document, page, tabs, panels, savePanel, controller};
}

function assertSelected(h, key) {
  for (let index = 0; index < keys.length; index += 1) {
    const active = keys[index] === key;
    assert.equal(h.tabs[index].attributes['aria-selected'], String(active), keys[index]);
    assert.equal(h.tabs[index].attributes.tabindex, active ? '0' : '-1', keys[index]);
    assert.equal(h.panels[index].hidden, !active, keys[index]);
  }
  assert.equal(h.savePanel.hidden, !['recognition', 'rules'].includes(key));
}

test('settings tabs initialize to recognition and clicking switches mounted panels', () => {
  const h = harness();
  assertSelected(h, 'recognition');
  for (const [index, key] of keys.entries()) {
    h.tabs[index].fire('click');
    assertSelected(h, key);
  }
  assert.equal(h.controller.activate('missing'), false);
  assertSelected(h, 'data');
});

test('horizontal keyboard navigation wraps and Home/End move focus with selection', () => {
  const h = harness();
  assert.equal(h.tabs[0].fire('keydown', {key: 'ArrowLeft'}).defaultPrevented, true);
  assertSelected(h, 'data');
  assert.equal(h.tabs[3].focusCount, 1);
  h.tabs[3].fire('keydown', {key: 'ArrowRight'});
  assertSelected(h, 'recognition');
  assert.equal(h.tabs[0].focusCount, 1);
  h.tabs[0].fire('keydown', {key: 'ArrowRight'});
  assertSelected(h, 'rules');
  assert.equal(h.tabs[1].focusCount, 1);
  h.tabs[1].fire('keydown', {key: 'End'});
  assertSelected(h, 'data');
  h.tabs[3].fire('keydown', {key: 'Home'});
  assertSelected(h, 'recognition');
  for (const key of ['Tab', 'ArrowDown', 'Enter']) {
    assert.equal(h.tabs[0].fire('keydown', {key}).defaultPrevented, false);
    assertSelected(h, 'recognition');
  }
});

test('switching tabs preserves form values and does not emit a configuration change', () => {
  const h = harness();
  const input = {value: '30', checked: true};
  h.panels[0].input = input;
  let changes = 0;
  h.panels[0].addEventListener('change', () => { changes += 1; });
  h.tabs[2].fire('click');
  h.tabs[0].fire('click');
  assert.equal(h.panels[0].input, input);
  assert.deepEqual(input, {value: '30', checked: true});
  assert.equal(changes, 0);
});

test('reinitializing is idempotent and retains the selected tab', () => {
  const h = harness();
  h.tabs[1].fire('click');
  assert.equal(initializeSettingsTabs(h.document), h.controller);
  assertSelected(h, 'rules');
  h.tabs.forEach(tab => {
    assert.equal(tab.listenerCount('click'), 1);
    assert.equal(tab.listenerCount('keydown'), 1);
  });
});

test('the current settings tab survives reloads and invalid saved values use recognition', () => {
  const saved = new Map([['receipt-settings-tab', 'services']]);
  const defaultView = {sessionStorage: {
    getItem(key) { return saved.get(key); },
    setItem(key, value) { saved.set(key, value); },
  }};
  const h = harness({defaultView});
  assertSelected(h, 'services');
  h.tabs[3].fire('click');
  assert.equal(saved.get('receipt-settings-tab'), 'data');
  assertSelected(harness({defaultView}), 'data');
  saved.set('receipt-settings-tab', 'unsupported');
  assertSelected(harness({defaultView}), 'recognition');
});

test('unavailable session storage does not prevent tab initialization or switching', () => {
  const deniedStorage = Object.defineProperty({}, 'sessionStorage', {get() { throw Error('Access denied'); }});
  const failingStorage = {sessionStorage: {
    getItem() { throw Error('Read failed'); },
    setItem() { throw Error('Write failed'); },
  }};
  for (const defaultView of [deniedStorage, failingStorage]) {
    const h = harness({defaultView});
    assertSelected(h, 'recognition');
    assert.doesNotThrow(() => h.tabs[2].fire('click'));
    assertSelected(h, 'services');
  }
});

test('documents without settings tabs are safe to initialize', () => {
  assert.doesNotThrow(() => initializeSettingsTabs(null));
  assert.doesNotThrow(() => initializeSettingsTabs({querySelector: () => null}));
  assert.doesNotThrow(() => initializeSettingsTabs({querySelector: () => ({querySelector: () => null})}));
});
