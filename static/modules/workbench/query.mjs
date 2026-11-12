/** Daily queue data, response ordering and freshness. No DOM or navigation.
 * The controller supplies the selected day and can reject responses after its
 * selection changes, even before the next request begins. */
export function createDailyQuery({progressState, services, ReceiptQueue, ReceiptWorkbench,
  now = () => Date.now(), onStateChange = () => {},
  onResultsChanged = () => {}, onQueueChanged = () => {}}) {
  async function load(day, {refreshRecords = true, isCurrent = () => true} = {}) {
    progressState.dailyAttemptAt = now();
    const requestId = progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    const previousDay = progressState.queueDay, newDay = previousDay !== day;
    const current = () => requestId === progressState.dailyRequest && isCurrent();
    if (newDay) {
      progressState.workItems = [];
      progressState.queueJobs = new Map();
      progressState.dailyReady = false;
    }
    onStateChange({phase: 'loading', day, newDay});
    try {
      const [jobs, control] = await Promise.all([
        services.queue.list(day),
        services.queue.control()
      ]);
      const signature = jobs.filter(job => ['succeeded', 'failed', 'cancelled'].includes(job.status)).map(job => `${job.id}:${job.status}:${job.finished_at}:${job.danzhengtong?.status || ''}:${job.danzhengtong?.updated_at || ''}`).join('|');
      const cached = progressState.progressRecordCache;
      const reuse = !refreshRecords && cached?.day === day && cached.signature === signature && now() - cached.at < 30000;
      const records = reuse ? cached.records : await (services.records.daily(day));
      if (!current()) return;
      if (!reuse) progressState.progressRecordCache = {day, signature, records, at: now()};
      progressState.queueControl = control;
      if (control.danzhengtong) progressState.dztControl = control.danzhengtong;
      progressState.queueJobs = new Map(jobs.map(job => [job.id, job]));
      const items = ReceiptQueue.rows(records, jobs), counts = ReceiptQueue.counts(items);
      counts.pending_review = items.filter(item => ReceiptWorkbench.category(item) === 'review').length;
      counts.passed = items.filter(item => ReceiptWorkbench.category(item) === 'passed').length;
      progressState.workItems = items;
      progressState.queueDay = day;
      progressState.dailyReady = true;
      onStateChange({phase: 'ready', day, newDay, counts, control});
      if (previousDay === day && progressState.queueSignature !== undefined && progressState.queueSignature !== signature) {
        onResultsChanged({keepProgressCache: true});
      }
      progressState.queueSignature = signature;
      const viewSignature = jobs.map(job => `${job.id}:${job.status}:${job.result_id || ''}:${job.target_result_id || ''}`).join('|');
      if (progressState.managementSignature !== `${day}:${viewSignature}`) {
        progressState.managementSignature = `${day}:${viewSignature}`;
        onQueueChanged();
      }
    } catch (error) {
      if (!current()) return;
      progressState.dailyReady = false;
      onStateChange({phase: 'error', day, newDay, error});
      throw error;
    } finally {
      // A selection change without a replacement request still releases loading.
      if (requestId === progressState.dailyRequest) onStateChange({phase: 'settled', day, newDay});
    }
  }

  return {load};
}
