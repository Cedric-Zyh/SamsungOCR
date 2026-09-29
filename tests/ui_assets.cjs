const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

test('template has one stable stylesheet entry and preserves feature order', () => {
  const template = fs.readFileSync('templates/index.html', 'utf8');
  const css = fs.readFileSync('static/ui.css', 'utf8');
  assert.equal((template.match(/rel="stylesheet"/g) || []).length, 1);
  assert.match(template, /asset_url\('ui\.css'\)/);
  for (const name of ['style.css', 'workspace.css', 'ui_refinements.css', 'settings.css', 'workbench_tasks.css', 'process_history.css']) {
    assert.match(css, new RegExp(`@import url\\('./styles/${name.replace('.', '\\.')}'\\)`));
    assert.equal(fs.existsSync(`static/styles/${name}`), true);
  }
});
