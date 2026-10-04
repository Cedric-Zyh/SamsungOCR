/** Daily queue query, response ordering and freshness tracking. */
import {localToday} from '../core/ui.mjs';

export function createDailyQuery({environment, ui, progressState, importsState, recordsState,
  reportState, resultsState, services, calendar, selected, viewState, pageSize,
  ReceiptQueue, ReceiptWorkbench, syncSelection, syncStartReview, syncStartRecognition,
  renderQueueControl, renderTask, renderWorkbenchRows, hasOnlyWorkFilter, setWorkFilter}) {
  const {document} = environment;
  const {$, $$} = ui;
  async function loadDailyResults({refreshRecords = true} = {}) {
    progressState.dailyAttemptAt = Date.now();
    const day = $('#progress-date').value || localToday();
    $('#progress-date').value = day;
    calendar.sync();
    const reviewEntry = $('#open-batch-review');
    if (document.body.dataset.activePage === 'progress' && reviewEntry.dataset.reviewDay !== day) {
      reviewEntry.dataset.reviewDay = day;
      reviewEntry.setAttribute('aria-label', `人工复核，${day}，当天全部`);
      reviewEntry.title = `复核 ${day} 当天的回单`;
      reviewEntry.classList.remove('has-pending');
      $('#review-entry-count').textContent = '';
      $('#review-entry-count').classList.add('hidden');
    }
    const requestId = progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    const previousDay = progressState.queueDay, newDay = previousDay !== day;
    if (newDay) {
      selected.clear(); viewState.shownItems = [];
      if ($('#overview-completed')) $('#overview-completed').textContent = '—';
      if ($('#overview-total')) $('#overview-total').textContent = '—';
      if ($('#overview-percent')) $('#overview-percent').textContent = '—';
      if ($('#overview-remaining')) $('#overview-remaining').textContent = '正在加载当天数据…';
      if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = '加载中';
      if ($('#workbench-type-counts')) $('#workbench-type-counts').innerHTML = '';
      $$('[data-work-count]').forEach(label => label.textContent = '—');
      progressState.dailyReady = false;
      syncSelection();
      progressState.workVisibleLimit = pageSize;
      $('#task-section').classList.add('hidden');
      $('#task-summary').textContent = '正在加载';
      $('#task-summary').dataset.status = 'loading';
      $('#task-progress').style.width = '0%';
      $('#task-progress').parentElement.setAttribute('aria-valuenow', 0);
      if ($('#workbench-total-label')) $('#workbench-total-label').textContent = '';
      $('#progress-note').textContent = `正在加载 ${day} 的回单…`;
    }
    syncStartReview();
    syncStartRecognition();
    $('#progress-note').dataset.tone = '';
    $('#task-section').setAttribute('aria-busy', 'true');
    try {
      const [jobs, control] = await Promise.all([
        services.queue.list(day),
        services.queue.control()
      ]);
      const signature = jobs.filter(job => ['succeeded', 'failed', 'cancelled'].includes(job.status)).map(job => `${job.id}:${job.status}:${job.finished_at}`).join('|');
      const cached = progressState.progressRecordCache;
      const reuse = !refreshRecords && cached?.day === day && cached.signature === signature && Date.now() - cached.at < 30000;
      const records = reuse ? cached.records : await (services.records.daily(day));
      if (requestId !== progressState.dailyRequest || $('#progress-date').value !== day) return;
      if (!reuse) progressState.progressRecordCache = {day, signature, records, at: Date.now()};
      progressState.queueControl = control;
      renderQueueControl();
      progressState.queueJobs = new Map(jobs.map(job => [job.id, job]));
      const items = ReceiptQueue.rows(records, jobs), counts = ReceiptQueue.counts(items);
      counts.pending_review = items.filter(item => ReceiptWorkbench.category(item) === 'review').length;
      counts.passed = items.filter(item => ReceiptWorkbench.category(item) === 'passed').length;
      if (!progressState.workInitialFilterResolved) {
        if (!progressState.workFilterChosen && hasOnlyWorkFilter('review') && counts.ready && !counts.pending_review) setWorkFilter('processing');
        progressState.workInitialFilterResolved = true;
      }
      if (control.paused) counts.status = control.status === 'pausing' ? '暂停中' : '已暂停';
      $('#task-section').classList.remove('hidden');
      renderTask(counts);
      // Keep visible receipts in place when a polling response adds newer jobs.
      const key = item => item.id ? `record:${item.id}` : `job:${item.job?.id}`;
      const ranks = new Map((newDay ? [] : progressState.workItems || []).map((item, index) => [key(item), index]));
      progressState.workItems = items.sort((a, b) => (ranks.get(key(a)) ?? Infinity) - (ranks.get(key(b)) ?? Infinity));
      progressState.queueDay = day;
      progressState.dailyReady = true;
      renderWorkbenchRows();
      $('#progress-note').textContent = importsState.batchRunning || importsState.uploadingJobs?.size ? '正在上传，请保持页面打开。' : '';
      $('#refresh-progress').textContent = '刷新';
      if (previousDay === day && progressState.queueSignature !== undefined && progressState.queueSignature !== signature) {
        recordsState.recordsStale = reportState.reportStale = true;
        resultsState.resultRevision = (resultsState.resultRevision || 0) + 1;
      }
      progressState.queueDay = day; progressState.queueSignature = signature;
    } catch (error) {
      if (requestId !== progressState.dailyRequest || $('#progress-date').value !== day) return;
      progressState.dailyReady = false;
      syncSelection();
      if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = '更新失败';
      syncStartReview();
      syncStartRecognition();
      if (newDay) { $('#task-summary').textContent = '暂时无法加载'; $('#task-summary').dataset.status = 'error'; }
      $('#progress-note').dataset.tone = 'danger';
      $('#progress-note').textContent = newDay ? `${day} 的回单加载失败，请重试。` : '更新失败，当前显示上次成功加载的回单，请重试。';
      $('#refresh-progress').textContent = '重试';
      throw error;
    } finally {
      if (requestId === progressState.dailyRequest) $('#task-section').removeAttribute('aria-busy');
    }
  }

  return {loadDailyResults};
}
