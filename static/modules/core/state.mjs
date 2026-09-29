/** Each controller owns one state bag. Other controllers receive only the
 * bags needed for cross-page workflows; request generations stay with their owner. */
export function createState() {
  const state = {
    records: {batchFilter: '', records: [], recordPage: 1, recordPageSize: 100, selected: new Set()},
    review: {reviewDeferred: new Set(), reviewDirty: false, reviewSaving: false, reviewEditVersion: 0,
      current: null, artifactTab: 'date', reviewQueue: [], reviewTask: ''},
    imports: {batchRunning: false, taskId: '', ocrBackend: '', sealRecognitionMode: 'local'},
    // `workFilter` remains for compatibility with queued actions and older
    // persisted state. `workFilters` is null until the user makes a choice,
    // then contains the selected status cards.
    progress: {workFilter: 'review', workFilters: null},
    report: {},
    navigation: {},
    results: {},
  };
  const listeners = new Map();
  const store = {
    getSlice(name) { return state[name]; },
    patch(name, updater) {
      const current = state[name];
      const next = typeof updater === 'function' ? updater(current) : {...current, ...updater};
      if (next && next !== current && current && typeof current === 'object' && !Array.isArray(current)) {
        Object.assign(current, next);
      } else if (next && next !== current) state[name] = next;
      for (const listener of listeners.get(name) || []) listener(state[name]);
      return state[name];
    },
    subscribe(name, listener) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(listener);
      return () => listeners.get(name)?.delete(listener);
    },
  };
  // Keep the existing slice-shaped API for feature controllers while making
  // cross-feature writes observable and explicit at the composition boundary.
  Object.defineProperty(state, 'store', {value: store, enumerable: false});
  return state;
}
