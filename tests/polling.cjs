const assert = require('node:assert/strict');
const test = require('node:test');
const {createPolling} = require('../static/modules/core/polling.mjs');

test('polling schedules one follow-up and never overlaps runs', async () => {
  const timers = [];
  const clearCalls = [];
  let resolveRun;
  const pending = new Promise(resolve => { resolveRun = resolve; });
  let calls = 0;
  const polling = createPolling({
    setTimeout: (callback, delay) => { timers.push({callback, delay}); return timers.length; },
    clearTimeout: id => clearCalls.push(id),
    run: async () => { calls += 1; await pending; },
    getDelay: () => 2000,
  });
  polling.start(2000);
  assert.equal(timers.length, 1);
  const first = polling.tick();
  assert.equal(polling.running, true);
  await polling.tick();
  assert.equal(calls, 1);
  resolveRun();
  await first;
  assert.equal(timers.at(-1).delay, 2000);
  polling.stop();
  assert.equal(polling.active, false);
  assert(clearCalls.length >= 1);
});
