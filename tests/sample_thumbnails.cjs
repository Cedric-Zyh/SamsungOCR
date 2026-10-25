const test = require('node:test');
const assert = require('node:assert/strict');
const {createSampleThumbnails} = require('../static/modules/samples/thumbnails.mjs');

const flush = () => new Promise(resolve => setImmediate(resolve));
const pictures = prefix => Array.from({length: 6}, (_, index) => ({dataset: {sampleSrc: `${prefix}-${index}`}}));

function harness() {
  const timers = new Map(), requests = [], created = [], revoked = [];
  let timerId = 0;
  const environment = {
    AbortController,
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(source, {signal}) {
      let resolve, reject;
      const promise = new Promise((a, b) => { resolve = a; reject = b; });
      // Deliberately allow an aborted transport to finish to exercise stale guards.
      requests.push({source, signal, reject, complete: () => resolve({ok: true, blob: async () => ({source})})});
      return promise;
    },
    window: {URL: {
      createObjectURL(blob) { const url = `blob:${blob.source}`; created.push(url); return url; },
      revokeObjectURL(url) { revoked.push(url); },
    }},
  };
  const loader = createSampleThumbnails({environment});
  const runTimer = () => { const [id, callback] = [...timers].at(-1); timers.delete(id); callback(); };
  return {loader, timers, requests, created, revoked, runTimer};
}

test('rapid paging aborts old requests and only starts the final page after the delay', async () => {
  const h = harness(), first = pictures('first'), second = pictures('second'), final = pictures('final');
  h.loader.replace(first);
  assert.equal(h.requests.length, 2);
  h.loader.replace(second, {defer: true});
  const obsoleteTimer = [...h.timers.values()][0];
  assert(h.requests.every(request => request.signal.aborted));
  h.loader.replace(final, {defer: true});
  assert.equal(h.timers.size, 1);
  obsoleteTimer();
  assert.equal(h.requests.length, 2);
  h.loader.replace(final);
  assert.equal(h.requests.length, 2);
  assert.equal(h.timers.size, 1);
  h.runTimer();
  assert.deepEqual(h.requests.slice(2).map(request => request.source), ['final-0', 'final-1']);
  h.requests[0].complete(); h.requests[1].complete();
  await flush();
  assert.deepEqual(h.created, []);
  assert(first.every(image => !image.src));
  h.requests[2].complete();
  await flush();
  assert.equal(final[0].src, 'blob:final-0');
  assert.deepEqual(h.requests.slice(4).map(request => request.source), ['final-2']);
  h.loader.clear();
});

test('thumbnail concurrency stays bounded and a redraw reuses and releases current image URLs', async () => {
  const h = harness(), first = pictures('first');
  h.loader.replace(first);
  for (let index = 0; index < 6; index++) {
    assert.equal(h.requests.length, Math.min(6, index + 2));
    h.requests[index].complete();
    await flush();
  }
  assert.equal(h.created.length, 6);
  const refreshed = pictures('first');
  h.loader.replace(refreshed);
  assert.equal(h.requests.length, 6);
  assert(refreshed.every((image, index) => image.src === first[index].src));
  assert.deepEqual(h.revoked, []);
  h.loader.replace(pictures('next'), {defer: true});
  assert.deepEqual(h.revoked, h.created);
  h.loader.clear();
  assert.equal(h.timers.size, 0);
});

test('polling redraw preserves a paging delay and failures allow the queue to continue', async () => {
  const h = harness();
  h.loader.replace(pictures('next'), {defer: true});
  h.loader.replace(pictures('next'));
  assert.equal(h.requests.length, 0);
  assert.equal(h.timers.size, 1);
  h.runTimer();
  h.requests[0].reject(Error('Network unavailable'));
  await flush();
  assert.equal(h.requests.length, 3);
  h.requests[1].complete();
  await flush();
  h.loader.clear();
  assert.deepEqual(h.revoked, ['blob:next-1']);
  assert(h.requests.slice(2).every(request => request.signal.aborted));
});

test('revisited pages skip the delay while refetching with cancellation and bounded concurrency', async () => {
  const h = harness();
  h.loader.replace(pictures('known'));
  for (let index = 0; index < 6; index++) {
    h.requests[index].complete();
    await flush();
  }
  h.loader.replace(pictures('unknown'), {defer: true});
  assert.equal(h.timers.size, 1);
  assert.deepEqual(h.revoked, h.created);
  h.loader.replace(pictures('known'), {defer: true});
  assert.equal(h.timers.size, 0);
  assert.equal(h.requests.length, 8);
  assert.deepEqual(h.requests.slice(6).map(request=>request.source), ['known-0', 'known-1']);
  assert(h.requests.slice(6).every(request=>!request.signal.aborted));
  const mixed = [...pictures('known').slice(0, 5), {dataset:{sampleSrc:'new'}}];
  h.loader.replace(mixed, {defer: true});
  assert(h.requests.slice(6).every(request=>request.signal.aborted));
  assert.equal(h.requests.length, 8);
  assert.equal(h.timers.size, 1);
  h.loader.clear();
});

test('recent source hints are bounded and survive URL cleanup without retaining image blobs', async () => {
  const h = harness();
  for (let index = 0; index < 129; index++) {
    h.loader.replace([{dataset:{sampleSrc:`known-${index}`}}]);
    h.requests[index].complete();
    await flush();
  }
  h.loader.clear();
  assert.deepEqual(h.revoked, h.created);
  h.loader.replace([{dataset:{sampleSrc:'known-0'}}], {defer: true});
  assert.equal(h.timers.size, 1);
  assert.equal(h.requests.length, 129);
  h.loader.clear();
  h.loader.replace([{dataset:{sampleSrc:'known-128'}}], {defer: true});
  assert.equal(h.timers.size, 0);
  assert.equal(h.requests.length, 130);
  h.loader.clear();
});
