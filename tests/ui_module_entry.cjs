const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

test('browser entry imports UI helpers instead of relying on classic globals', () => {
  const app = fs.readFileSync('static/app.js', 'utf8');
  const template = fs.readFileSync('templates/index.html', 'utf8');
  assert.match(app, /modules\/core\/import_files\.mjs/);
  assert.match(app, /modules\/core\/queue\.mjs/);
  assert.match(app, /modules\/core\/workbench\.mjs/);
  assert.doesNotMatch(template, /static', filename='(?:queue_state|workbench|import_files)\.js/);
  for (const file of ['static/workbench.js', 'static/queue_state.js', 'static/import_files.js']) {
    assert.equal(fs.existsSync(file), false, `${file} should no longer be a browser entrypoint`);
  }
});

test('domain API clients keep endpoint construction out of feature views', async () => {
  const {createApiClients} = await import('../static/modules/core/api.mjs');
  const calls = [];
  const api = async (url, options) => { calls.push({url, options}); return {ok: true}; };
  const clients = createApiClients(api);
  await clients.records.list({import_date: '2026-09-25', page: 2});
  await clients.records.review(7, {review_status: '确认通过'});
  await clients.settings.update({retention_days: 30});
  assert.equal(calls[0].url, '/api/results?import_date=2026-09-25&page=2');
  assert.deepEqual(calls[1].options, {method: 'PATCH', json: {review_status: '确认通过'}});
  assert.deepEqual(calls[2].options, {method: 'PATCH', json: {retention_days: 30}});
});


test('workbench and review coordinators delegate row and editor presentation', () => {
  const workbench = fs.readFileSync('static/modules/workbench/workbench.mjs', 'utf8');
  const review = fs.readFileSync('static/modules/review/review.mjs', 'utf8');
  assert.match(workbench, /row_renderer\.mjs/);
  assert.doesNotMatch(workbench, /const badge = \(label, value, status\)/);
  assert.match(review, /editors\.mjs/);
  assert.doesNotMatch(review, /function setupDateConfirmation\(/);
});

test('feature controllers do not own endpoint URL strings', () => {
  for (const file of [
    'static/modules/imports/imports.mjs',
    'static/modules/imports/import_calendar.mjs',
    'static/modules/records/records.mjs',
    'static/modules/records/process_history.mjs',
    'static/modules/review/review.mjs',
    'static/modules/review/review_evidence.mjs',
    'static/modules/review/review_queue.mjs',
    'static/modules/report/report.mjs',
    'static/modules/workbench/workbench.mjs',
  ]) {
    assert.doesNotMatch(fs.readFileSync(file, 'utf8'), /\/api\//, file);
  }
});
