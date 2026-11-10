/** Request/snapshot ownership for the sample directory. No DOM or navigation.
 * Snapshot application is synchronous; asynchronous related-panel work belongs
 * to onRefresh. The shell owns the poll timer. */
export function createSampleDataSession({list, now = () => Date.now(), isActive = () => false,
  onSnapshot = () => {}, onRefresh = () => {}, onError = () => {}}) {
  let snapshot = null, revision, pending = null, loaded = false, failed = false, attemptedAt = null;

  async function read(conditional) {
    attemptedAt = now();
    try {
      const data = await list(conditional && loaded ? revision : undefined);
      const changed = !data.unchanged;
      if (changed) {
        onSnapshot(data);
        snapshot = data;
        revision = data.revision;
        loaded = true;
      } else if (!loaded) {
        throw new Error('样单列表尚未加载，请重新刷新');
      }
      failed = false;
      await onRefresh({changed});
    } catch (error) {
      failed = true;
      await onError(error, {loaded});
    }
  }

  function load({force = false, conditional = false} = {}) {
    // A mutation may complete while an earlier read is pending. Forced loads
    // must read again afterward; ordinary callers share the in-flight read.
    if (pending) return force ? pending.then(() => load()) : pending;
    pending = read(conditional).finally(() => { pending = null; });
    return pending;
  }

  function poll() {
    const active = isActive() || !loaded || failed || snapshot?.items.some(item => item.status === '测试中');
    if (attemptedAt !== null && now() - attemptedAt < (active ? 2000 : 30000)) return pending;
    return load({conditional: true});
  }

  function markAccepted(filenames, mode) {
    snapshot = acceptedSampleRun(snapshot || {items: []}, filenames, mode);
    return snapshot;
  }

  return {load, poll, markAccepted, get snapshot() { return snapshot; }};
}

/** Optimistic submission state: preserve other providers only for remote-only
 * additions. The server snapshot and unrelated sample rows remain untouched. */
export function acceptedSampleRun(snapshot, filenames, mode) {
  const accepted = new Set(filenames);
  return {...snapshot, items: snapshot.items.map(item => {
    if (!accepted.has(item.filename)) return item;
    const updated = {...item, status: '测试中', job_status: 'queued', error: '', execution_mode: mode};
    if (mode === 'danzhengtong' && item.result_id) {
      updated.retained_results = true;
      updated.danzhengtong = {pending: true, status: 'queued', label: '等待提交'};
    } else {
      delete updated.danzhengtong;
      delete updated.result_filters;
      updated.retained_results = false;
    }
    return updated;
  })};
}
