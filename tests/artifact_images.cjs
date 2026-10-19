const test = require('node:test');
const assert = require('node:assert/strict');
const {bindArtifactImage} = require('../static/modules/review/artifact_images.mjs');

function fixture(complete = false, width = 0) {
  const image = new EventTarget();
  image.src = '/files/artifacts/stamp.png';
  image.getAttribute = () => image.src;
  image.complete = complete;
  image.naturalWidth = width;
  const retry = new EventTarget();
  const message = {};
  const host = {dataset: {}, setAttribute() {}};
  const timers = new Map();
  let next = 0;
  const cleanup = bindArtifactImage({image, retry, host, message,
    setTimer: fn => { timers.set(++next, fn); return next; },
    clearTimer: key => timers.delete(key),
  });
  return {image, retry, message, host, timers, cleanup};
}

test('already cached images show immediately and never leave a loading timer', () => {
  const f = fixture(true, 969);
  assert.equal(f.host.dataset.imageState, 'ready');
  assert.equal(f.message.hidden, true);
  assert.equal(f.retry.hidden, true);
  assert.equal(f.timers.size, 0);
});

test('failed and stalled images expose retry, which bypasses the failed cache entry', () => {
  const f = fixture();
  assert.equal(f.host.dataset.imageState, 'loading');
  [...f.timers.values()][0]();
  assert.equal(f.host.dataset.imageState, 'error');
  assert.equal(f.retry.hidden, false);
  f.retry.dispatchEvent(new Event('click'));
  assert.equal(f.host.dataset.imageState, 'loading');
  assert.match(f.image.src, /^\/files\/artifacts\/stamp.png\?_image_retry=/);
  const first = f.image.src;
  f.image.dispatchEvent(new Event('error'));
  assert.match(f.message.textContent, /重新加载/);
  f.retry.dispatchEvent(new Event('click'));
  assert.notEqual(f.image.src, first);
  f.image.naturalWidth = 969;
  f.image.dispatchEvent(new Event('load'));
  assert.equal(f.host.dataset.imageState, 'ready');
  assert.equal(f.timers.size, 0);
});

test('removing a rendered grid detaches image callbacks and cancels feedback timers', () => {
  const f = fixture();
  f.cleanup();
  assert.equal(f.timers.size, 0);
  f.image.dispatchEvent(new Event('error'));
  assert.equal(f.host.dataset.imageState, 'loading');
});
