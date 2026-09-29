/** Small lifecycle wrapper for page polling.
 * The feature decides what to load; this module owns one timer, cancellation,
 * overlap prevention and rescheduling. */
export function createPolling({setTimeout, clearTimeout, run, getDelay = () => 2000}) {
  let timer = null;
  let active = false;
  let running = false;

  function schedule(delay = getDelay()) {
    if (!active) return;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(tick, delay);
  }

  async function tick(force = false) {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if ((!active && !force) || running) return;
    running = true;
    try {
      return await run();
    } finally {
      running = false;
      if (active) schedule();
    }
  }

  function start(delay = getDelay()) {
    active = true;
    if (timer === null) schedule(delay);
  }

  function stop() {
    active = false;
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  return {start, stop, tick, get running() { return running; }, get active() { return active; }};
}
