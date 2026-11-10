const assert = require('node:assert/strict');
const test = require('node:test');
const {createState, createResultChanges} = require('../static/modules/core/state.mjs');

test('state exposes explicit slice patching without changing controller-facing bags', () => {
  const state = createState();
  const changes = [];
  const unsubscribe = state.store.subscribe('records', slice => changes.push(slice.recordsStale));
  state.store.patch('records', slice => ({...slice, recordsStale: true}));
  assert.equal(state.records.recordsStale, true);
  assert.deepEqual(changes, [true]);
  unsubscribe();
  state.store.patch('records', {recordsStale: false});
  assert.deepEqual(changes, [true]);
  assert.equal(Object.keys(state).includes('store'), false);
});

test('shared result changes invalidate every dependent view through one observable entry point', () => {
  const state = createState(), changes = createResultChanges(state), observed = [];
  state.progress.progressRecordCache = {day: '2026-11-10'};
  state.records.selected.add(7);
  state.review.current = {id: 7};
  for (const name of ['progress', 'records', 'report', 'results']) {
    state.store.subscribe(name, () => observed.push(name));
  }
  changes.resultsChanged();
  assert.equal(state.progress.progressRecordCache, null);
  assert.equal(state.records.recordsStale, true);
  assert.equal(state.report.reportStale, true);
  assert.equal(state.results.resultRevision, 1);
  assert.deepEqual(observed, ['progress', 'records', 'report', 'results']);
  assert.deepEqual([...state.records.selected], [7]);
  assert.equal(state.review.current.id, 7);
});

test('queue-only changes and freshly loaded result changes preserve the appropriate caches', () => {
  const state = createState(), changes = createResultChanges(state);
  const cached = state.progress.progressRecordCache = {records: []};
  changes.queueChanged();
  assert.equal(state.records.recordsStale, true);
  assert.equal(state.report.reportStale, undefined);
  assert.equal(state.results.resultRevision, undefined);
  assert.equal(state.progress.progressRecordCache, cached);
  changes.resultsChanged({keepProgressCache: true});
  assert.equal(state.progress.progressRecordCache, cached);
  assert.equal(state.results.resultRevision, 1);
});

test('plain injected state uses the same result invalidation policy', () => {
  const state = {progress: {}, records: {}, report: {}, results: {resultRevision: 3}};
  createResultChanges(state).resultsChanged();
  assert.deepEqual(state, {progress: {progressRecordCache: null}, records: {recordsStale: true},
    report: {reportStale: true}, results: {resultRevision: 4}});
});
