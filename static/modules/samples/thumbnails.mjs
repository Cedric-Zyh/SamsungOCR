/** Keep rapid paging from starting requests for every intermediate page. */
export function createSampleThumbnails({environment, delay = 180, concurrency = 2}) {
  const setTimer = environment.setTimeout || setTimeout;
  const clearTimer = environment.clearTimeout || clearTimeout;
  const fetchImage = environment.fetch || environment.window?.fetch?.bind(environment.window);
  const Url = environment.window?.URL;
  const Controller = environment.AbortController || globalThis.AbortController;
  let revision = 0, timer = null, queue = [];
  const pending = new Set(), objectUrls = new Map(), recent = new Set();

  function cancel() {
    revision++;
    if (timer !== null) clearTimer(timer);
    timer = null;
    queue = [];
    for (const controller of pending) controller.abort();
    pending.clear();
  }

  async function load(image, source, token) {
    const controller = new Controller();
    pending.add(controller);
    try {
      const response = await fetchImage(source, {signal: controller.signal});
      if (!response.ok) throw new Error('Thumbnail unavailable');
      const blob = await response.blob();
      // Aborted requests may still finish; never attach an obsolete page's image.
      if (token !== revision || controller.signal.aborted) return;
      const url = Url.createObjectURL(blob);
      objectUrls.set(source, url);
      image.src = url;
      recent.delete(source); recent.add(source);
      if (recent.size > 128) recent.delete(recent.values().next().value);
    } catch (_) {
      // Leave the filename/preview button usable when an image is unavailable.
    } finally {
      pending.delete(controller);
      if (token === revision) pump(token);
    }
  }

  function pump(token) {
    while (token === revision && pending.size < concurrency && queue.length) {
      const {image, source} = queue.shift();
      load(image, source, token);
    }
  }

  function replace(images, {defer = false} = {}) {
    const rows = Array.from(images);
    // Revisited pages still fetch/validate images, but need no cold-page delay.
    // A polling redraw must preserve the delay while any source is still unknown.
    const wait = (defer || timer !== null) && rows.some(image => !recent.has(image.dataset.sampleSrc));
    cancel();
    const token = revision;
    const sources = new Set(rows.map(image => image.dataset.sampleSrc));
    for (const [source, url] of objectUrls) if (!sources.has(source)) {
      Url.revokeObjectURL(url);
      objectUrls.delete(source);
    }
    for (const image of rows) {
      const source = image.dataset.sampleSrc;
      image.decoding = 'async';
      if (objectUrls.has(source)) image.src = objectUrls.get(source);
      else queue.push({image, source});
    }
    if (!queue.length) return;
    if (wait) timer = setTimer(() => {
      if (token !== revision) return;
      timer = null; pump(token);
    }, delay);
    else pump(token);
  }

  function clear() {
    cancel();
    for (const url of objectUrls.values()) Url.revokeObjectURL(url);
    objectUrls.clear();
  }

  return {replace, clear};
}
