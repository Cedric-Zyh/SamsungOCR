const assert = require('node:assert/strict');
const test = require('node:test');
const {createSampleDataSession, acceptedSampleRun} = require('../static/modules/samples/data_session.mjs');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
};
const snapshot = (revision, status = '未测试') => ({revision, companies: [], items: [{filename: 'one.jpg', status}]});

test('ordinary reads coalesce while a forced post-mutation read waits for a new snapshot', async () => {
  const old = deferred(), latest = deferred(), accepted = [], calls = [];
  const session = createSampleDataSession({list: revision => {
    calls.push(revision); return calls.length === 1 ? old.promise : latest.promise;
  }, onSnapshot: data => accepted.push(data.revision)});
  const first = session.load(), joined = session.load(), forced = session.load({force: true});
  assert.equal(joined, first);
  assert.equal(calls.length, 1);
  old.resolve(snapshot('old')); await first;
  assert.equal(calls.length, 2);
  latest.resolve(snapshot('new', '测试中')); await forced;
  assert.deepEqual(accepted, ['old', 'new']);
  assert.equal(session.snapshot.revision, 'new');
  assert.deepEqual(calls, [undefined, undefined]);
});

test('conditional polls preserve the snapshot and skip repeated rendering but refresh related panels', async () => {
  let now = 0;
  const calls = [], snapshots = [], refreshed = [], initial = snapshot('revision', '测试中');
  const session = createSampleDataSession({now: () => now, list: async revision => {
    calls.push(revision); return revision ? {unchanged: true, revision} : initial;
  }, onSnapshot: data => snapshots.push(data), onRefresh: event => refreshed.push(event.changed)});
  await session.load();
  now = 1999; await session.poll();
  assert.equal(calls.length, 1);
  now = 2000; await session.poll();
  assert.deepEqual(calls, [undefined, 'revision']);
  assert.deepEqual(snapshots, [initial]);
  assert.deepEqual(refreshed, [true, false]);
  assert.equal(session.snapshot, initial);
  await session.load();
  assert.equal(calls.at(-1), undefined, 'explicit reload always requests a full snapshot');
});

test('idle reads slow down, failures retain the last snapshot and retry promptly', async () => {
  let now = 0, fail = false, calls = 0;
  const errors = [], initial = snapshot('initial');
  const session = createSampleDataSession({now: () => now, list: async () => {
    calls++; if (fail) throw Error('offline'); return initial;
  }, onError: (error, status) => errors.push([error.message, status.loaded])});
  await session.load();
  now = 29999; await session.poll(); assert.equal(calls, 1);
  fail = true; now = 30000; await session.poll();
  assert.deepEqual(errors, [['offline', true]]);
  assert.equal(session.snapshot, initial);
  now = 31999; await session.poll(); assert.equal(calls, 2);
  fail = false; now = 32000; await session.poll(); assert.equal(calls, 3);
});

test('remote-only submission retains existing evidence and does not mutate a received snapshot', () => {
  const source = {revision: 'r1', items: [
    {filename: 'one.jpg', status: '已完成', result_id: 8, result_filters: {date: '匹配'}, error: 'old'},
    {filename: 'two.jpg', status: '未测试'},
  ]};
  const saved = structuredClone(source);
  const remote = acceptedSampleRun(source, ['one.jpg'], 'danzhengtong');
  assert.deepEqual(source, saved);
  assert.equal(remote.items[0].retained_results, true);
  assert.equal(remote.items[0].result_filters, source.items[0].result_filters);
  assert.equal(remote.items[0].danzhengtong.pending, true);
  assert.equal(remote.items[0].error, '');
  assert.equal(remote.items[1], source.items[1]);
  const full = acceptedSampleRun(remote, ['one.jpg'], 'all');
  assert.equal(full.items[0].retained_results, false);
  assert(!('danzhengtong' in full.items[0]));
  assert(!('result_filters' in full.items[0]));
  assert.equal(remote.items[0].danzhengtong.pending, true);
});

test('accepted jobs activate polling even before the next server snapshot', async () => {
  let now = 0, calls = 0;
  const session = createSampleDataSession({now: () => now, list: async () => {calls++; return snapshot('r1');}});
  await session.load();
  session.markAccepted(['one.jpg'], 'local');
  assert.equal(session.snapshot.items[0].status, '测试中');
  now = 2000; await session.poll();
  assert.equal(calls, 2);
});

test('a failed snapshot application cannot acknowledge a revision that was not displayed', async () => {
  let fail = false;
  const revisions = [], errors = [];
  const session = createSampleDataSession({list: async revision => {
    revisions.push(revision); return snapshot(revisions.length === 1 ? 'old' : 'new');
  }, onSnapshot: () => { if (fail) throw Error('view failed'); }, onError: error => errors.push(error.message)});
  await session.load();
  fail = true; await session.load({conditional: true});
  assert.equal(session.snapshot.revision, 'old');
  fail = false; await session.load({conditional: true});
  assert.deepEqual(revisions, [undefined, 'old', 'old']);
  assert.deepEqual(errors, ['view failed']);
  assert.equal(session.snapshot.revision, 'new');
});
