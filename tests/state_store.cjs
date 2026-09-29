const assert = require('node:assert/strict');
const test = require('node:test');
const {createState} = require('../static/modules/core/state.mjs');

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
