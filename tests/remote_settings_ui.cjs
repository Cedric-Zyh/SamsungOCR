const test = require('node:test');
const assert = require('node:assert/strict');
const {createRemoteSettings} = require('../static/modules/imports/remote_settings.mjs');
const {sealWorkspaceModel} = require('../static/modules/review/seal_workspace.mjs');

test('saving the download switch sends JSON booleans for both checkbox states', async () => {
  for (const checked of [false, true]) {
    const nodes = new Map(), calls = [];
    const node = () => ({textContent:'', addEventListener(event, listener) { this[event] = listener; }});
    for (const id of ['dzt-config-form', 'dzt-test-form', 'dzt-save-status', 'dzt-test-file', 'dzt-clear']) {
      nodes.set('#' + id, node());
    }
    const form = nodes.get('#dzt-config-form');
    form.elements = {namedItem(name) { assert.equal(name, 'download_seal_images'); return {checked}; }};
    const environment = {
      window:{FormData:class { *[Symbol.iterator]() { yield ['poll_timeout_seconds', '60']; } }},
      document:{querySelectorAll:() => []}, location:{reload() {}},
    };
    createRemoteSettings({environment, ui:{$:key => nodes.get(key), $$:() => []},
      api:async (url, options) => { calls.push({url, options}); return {}; }}).initialize();
    form.submit({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/settings/danzhengtong');
    assert.equal(calls[0].options.json.download_seal_images, checked);
    assert.equal(calls[0].options.json.poll_timeout_seconds, 60);
  }
});

test('pending and disabled images explain their actual state while retaining text', () => {
  for (const [status, message] of [['pending', '正在后台下载'], ['disabled', '已关闭']]) {
    const model = sealWorkspaceModel({provider:'danzhengtong', provider_text:'测试客户',
      returned_image_status:status, input_url:'/files/enhanced.png'});
    assert.equal(model.text, '测试客户');
    assert.equal(model.processed, '');
    assert.equal(model.preferred, '/files/enhanced.png');
    assert.ok(model.warning.includes(message));
    assert.ok(!model.warning.includes('未能取回'));
  }
});
