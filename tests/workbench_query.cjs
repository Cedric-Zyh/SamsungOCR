const assert = require('node:assert/strict');
const test = require('node:test');
const {createDailyQuery} = require('../static/modules/workbench/query.mjs');
const {createState, createResultChanges} = require('../static/modules/core/state.mjs');
const {ReceiptQueue} = require('../static/modules/core/queue.mjs');
const {ReceiptWorkbench} = require('../static/modules/core/workbench.mjs');

const day = '2026-09-10', nextDay = '2026-09-11';
const row = id => ({id, filename: `${id}.jpg`, fields: {}, review_status: '待复核'});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function harness() {
  const state = createState(), requests = [], transitions = [], notifications = [];
  const changes = createResultChanges(state);
  const h = {state, requests, transitions, notifications, clock: 100000,
    jobs: [], control: {paused: false}, records: [row(1)], readRecords: () => h.records};
  h.query = createDailyQuery({progressState: state.progress, ReceiptQueue, ReceiptWorkbench,
    now: () => h.clock,
    services: {
      queue: {
        list: async value => { requests.push(['queue', value]); return h.jobs; },
        control: async () => { requests.push(['control']); return h.control; },
      },
      records: {daily: async value => { requests.push(['records', value]); return h.readRecords(value); }},
    },
    onStateChange: event => transitions.push(event),
    onResultsChanged: options => { notifications.push(['results', options]); changes.resultsChanged(options); },
    onQueueChanged: () => { notifications.push(['queue']); changes.queueChanged(); },
  });
  h.recordReads = () => requests.filter(([kind]) => kind === 'records').length;
  return h;
}

test('daily data loads for its explicit date without a document or renderer', async () => {
  const h = harness();
  h.control = {paused: true, status: 'pausing', danzhengtong: {submit_paused: true}};
  await h.query.load(day);
  assert.deepEqual(h.requests, [['queue', day], ['control'], ['records', day]]);
  assert.equal(h.state.progress.queueDay, day);
  assert.equal(h.state.progress.dailyReady, true);
  assert.equal(h.state.progress.dailyAttemptAt, h.clock);
  assert.equal(h.state.progress.workItems[0].id, 1);
  assert.equal(h.state.progress.dztControl, h.control.danzhengtong);
  assert.deepEqual(h.transitions.map(event => event.phase), ['loading', 'ready', 'settled']);
  const ready = h.transitions[1];
  assert.equal(ready.counts.pending_review, 1);
  assert.equal(ready.control, h.control);
  assert.deepEqual(h.notifications, [['queue']]);
});

test('polling reuses recent records while manual refresh, expiry and invalidation refetch', async () => {
  const h = harness();
  h.records = [];
  h.jobs = [{id: 'running', filename: 'run.jpg', status: 'running', progress: {elapsed_seconds: 1}}];
  await h.query.load(day);
  h.jobs = [{...h.jobs[0], progress: {elapsed_seconds: 2}}];
  await h.query.load(day, {refreshRecords: false});
  assert.equal(h.recordReads(), 1);
  assert.equal(h.state.progress.queueJobs.get('running').progress.elapsed_seconds, 2);
  await h.query.load(day);
  assert.equal(h.recordReads(), 2);
  h.clock += 30000;
  await h.query.load(day, {refreshRecords: false});
  assert.equal(h.recordReads(), 3);
  createResultChanges(h.state).resultsChanged();
  await h.query.load(day, {refreshRecords: false});
  assert.equal(h.recordReads(), 4);
  await h.query.load(nextDay, {refreshRecords: false});
  assert.equal(h.recordReads(), 5);
  assert.equal(h.state.progress.progressRecordCache.day, nextDay);
});

test('completion and remote updates refetch results and notify without discarding the accepted cache', async () => {
  const h = harness(); h.records = [];
  h.jobs = [{id: 'one', filename: 'one.jpg', status: 'running'}];
  await h.query.load(day);
  h.jobs = [{...h.jobs[0], progress: {elapsed_seconds: 5}}];
  await h.query.load(day, {refreshRecords: false});
  assert.deepEqual(h.notifications, [['queue']]);
  h.jobs = [{...h.jobs[0], status: 'succeeded', result_id: 1, finished_at: 'first',
    danzhengtong: {status: 'waiting', updated_at: 'first'}}];
  await h.query.load(day, {refreshRecords: false});
  assert.equal(h.recordReads(), 2);
  assert.equal(h.state.results.resultRevision, 1);
  assert.equal(h.state.progress.progressRecordCache.records, h.records);
  assert.deepEqual(h.notifications.at(-2), ['results', {keepProgressCache: true}]);
  assert.deepEqual(h.notifications.at(-1), ['queue']);
  h.jobs = [{...h.jobs[0], danzhengtong: {status: 'applied', updated_at: 'second'}}];
  await h.query.load(day, {refreshRecords: false});
  assert.equal(h.recordReads(), 3);
  assert.equal(h.state.results.resultRevision, 2);
  assert.deepEqual(h.notifications.at(-1), ['results', {keepProgressCache: true}]);
});

for (const responseDay of [day, nextDay]) {
  for (const outcome of ['success', 'failure']) {
    test(`newest ${responseDay === day ? 'same-day' : 'different-day'} data survives an older ${outcome}`, async () => {
      const h = harness(), older = deferred(), started = deferred();
      h.readRecords = () => { started.resolve(); return older.promise; };
      const previous = h.query.load(day);
      await started.promise;
      h.clock += 1;
      h.records = [row(2)]; h.readRecords = () => h.records;
      await h.query.load(responseDay);
      const acceptedCache = h.state.progress.progressRecordCache;
      if (outcome === 'success') older.resolve([row(1)]);
      else older.reject(Error('old request failed'));
      await previous;
      assert.equal(h.state.progress.queueDay, responseDay);
      assert.equal(h.state.progress.workItems[0].id, 2);
      assert.equal(h.state.progress.dailyReady, true);
      assert.equal(h.state.progress.progressRecordCache, acceptedCache);
      assert.deepEqual(h.transitions.map(event => event.phase), ['loading', 'loading', 'ready', 'settled']);
      assert.deepEqual(h.notifications, [['queue']]);
    });
  }
}

test('selection changes can reject a response before any replacement query starts', async () => {
  const h = harness(), waiting = deferred(), started = deferred();
  let selectedDay = day;
  h.readRecords = () => { started.resolve(); return waiting.promise; };
  const pending = h.query.load(day, {isCurrent: () => selectedDay === day});
  await started.promise;
  selectedDay = nextDay;
  waiting.resolve([row(1)]);
  await pending;
  assert.equal(h.state.progress.queueDay, undefined);
  assert.equal(h.state.progress.progressRecordCache, undefined);
  assert.equal(h.state.progress.dailyReady, false);
  assert.deepEqual(h.state.progress.workItems, []);
  assert.deepEqual(h.transitions.map(event => event.phase), ['loading', 'settled']);
  assert.deepEqual(h.notifications, []);
});

test('same-day failure retains data while switching days clears it before the request finishes', async () => {
  const h = harness();
  await h.query.load(day);
  const previous = h.state.progress.workItems, cache = h.state.progress.progressRecordCache;
  h.readRecords = () => { throw Error('offline'); };
  await assert.rejects(h.query.load(day), /offline/);
  assert.equal(h.state.progress.workItems, previous);
  assert.equal(h.state.progress.progressRecordCache, cache);
  assert.equal(h.state.progress.dailyReady, false);
  assert.equal(h.transitions.at(-2).newDay, false);
  const waiting = deferred(); h.readRecords = () => waiting.promise;
  const pending = h.query.load(nextDay);
  assert.deepEqual(h.state.progress.workItems, []);
  assert.equal(h.state.progress.queueJobs.size, 0);
  assert.equal(h.state.progress.dailyReady, false);
  waiting.reject(Error('offline'));
  await assert.rejects(pending, /offline/);
  assert.equal(h.transitions.at(-2).newDay, true);
  assert.equal(h.state.progress.queueDay, day);
  assert.equal(h.state.progress.progressRecordCache, cache);
});
