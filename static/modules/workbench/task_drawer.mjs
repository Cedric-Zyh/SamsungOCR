import {escapeHtml} from '../core/ui.mjs';

/** A task-only surface. It never owns receipt result filters or review state. */
export function createTaskDrawer({environment, ui, progressState, renderWorkbenchRows, openManagedRecords}) {
  const {$, $$} = ui;
  const {document, window} = environment;
  let trigger;
  const tabs = {processing: ['queued', 'running'], ready: ['ready'], failed: ['failed']};
  function chooseTab(tab) {
    progressState.workFilters = tabs[tab] || tabs.processing;
    progressState.workFilter = tab;
    progressState.workSearch = '';
    progressState.workVisibleLimit = 50;
    if ($('#workbench-search')) $('#workbench-search').value = '';
    renderWorkbenchRows();
  }
  function close() { $('#task-drawer')?.close(); }
  function open(jobId = '') {
    const node = $('#task-drawer');
    if (!node?.showModal) return;
    trigger = document.activeElement;
    const job = progressState.queueJobs?.get(jobId);
    if (job) {
      progressState.taskBatch = job.task_id;
      chooseTab(job.status === 'failed' ? 'failed' : ['ready', 'awaiting_upload'].includes(job.status) ? 'ready' : 'processing');
    } else {
      const jobs = [...(progressState.queueJobs?.values() || [])];
      chooseTab(jobs.some(item => ['running', 'queued'].includes(item.status)) ? 'processing'
        : jobs.some(item => ['ready', 'awaiting_upload'].includes(item.status)) ? 'ready'
        : jobs.some(item => item.status === 'failed') ? 'failed' : 'ready');
    }
    if (!node.open) node.showModal();
    document.body.classList.add('task-drawer-open');
    if (jobId) [...node.querySelectorAll('[data-task-job]')].find(item => item.dataset.taskJob === jobId)?.scrollIntoView({block: 'nearest'});
  }
  function syncBatches(items) {
    const control = $('#task-batch-select');
    if (!control) return;
    const batches = new Map();
    for (const {job} of items) if (job && !batches.has(job.task_id)) batches.set(job.task_id, job.created_at);
    if (progressState.taskBatch && !batches.has(progressState.taskBatch)) progressState.taskBatch = '';
    const markup = '<option value="">当天全部批次</option>' + [...batches].map(([id, at], index) => `<option value="${escapeHtml(id)}">${escapeHtml(at?.replace('T', ' ').slice(5, 16) || '导入批次')} · 批次 ${batches.size - index}</option>`).join('');
    if (control.innerHTML !== markup) control.innerHTML = markup;
    control.value = progressState.taskBatch || '';
  }
  function renderSummary(allItems, items) {
    const count = rows => {
      const counts = {ready: 0, queued: 0, running: 0, failed: 0, succeeded: 0};
      for (const row of rows) {
        const key = row.status === 'awaiting_upload' ? 'ready' : row.status;
        if (key in counts) counts[key]++;
      }
      return counts;
    };
    const all = count(allItems), current = count(items);
    if ($('#task-strip-scope')) $('#task-strip-scope').textContent = `${$('#progress-date').value} · 全部批次`;
    if ($('#workbench-state-pill') && progressState.dailyReady) $('#workbench-state-pill').textContent = progressState.queueControl?.paused ? (progressState.queueControl.status === 'pausing' ? '暂停中' : '已暂停') : all.running ? '正在识别' : all.queued ? '排队中' : all.ready ? '等待开始' : allItems.length ? '识别已结束' : '暂无任务';
    if ($('#task-strip-counts')) $('#task-strip-counts').textContent = all.ready + all.queued + all.running + all.failed
      ? `待开始 ${all.ready} · 排队 ${all.queued} · 识别中 ${all.running}${all.failed ? ` · 失败 ${all.failed}` : ''}`
      : all.succeeded ? `识别已结束 · ${all.succeeded} 张` : '导入回单后开始识别';
    if ($('#task-drawer-day')) $('#task-drawer-day').textContent = $('#progress-date').value;
    for (const [id, value] of Object.entries({'task-count-succeeded': current.succeeded, 'task-count-failed': current.failed, 'task-count-pending': current.ready + current.queued + current.running})) {
      if ($(`#${id}`)) $(`#${id}`).textContent = value.toLocaleString();
    }
    if ($('#task-batch-title')) $('#task-batch-title').textContent = `${progressState.taskBatch ? '当前批次' : '当天全部批次'} · ${items.length} 张`;
    if ($('#overview-remaining')) $('#overview-remaining').textContent = `成功 ${current.succeeded} · 失败 ${current.failed} · 识别中 ${current.running} · 排队 ${current.queued} · 待开始 ${current.ready}`;
    if ($('#task-failure-notice')) {
      $('#task-failure-notice').classList.toggle('hidden', !current.failed);
      $('#task-failure-notice span').textContent = `${current.failed} 张识别失败，可查看原因并重试`;
    }
    const filters = progressState.workFilters || [];
    $$('[data-task-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.taskTab === (filters.includes('failed') ? 'failed' : filters.length === 1 && filters[0] === 'ready' ? 'ready' : 'processing'))));
    const processing = $('[data-work-count="processing"]');
    if (processing) processing.textContent = current.queued + current.running;
  }
  function initialize() {
    $('#open-task-drawer')?.addEventListener('click', () => open());
    $('#close-task-drawer')?.addEventListener('click', close);
    const node = $('#task-drawer');
    node?.addEventListener('close', () => {
      document.body.classList.remove('task-drawer-open');
      if (trigger?.isConnected) trigger.focus({preventScroll: true});
      else $('#open-task-drawer')?.focus({preventScroll: true});
    });
    node?.addEventListener('click', event => {
      if (event.target !== node) return;
      const rect = node.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
    });
    $('#task-batch-select')?.addEventListener('change', event => { progressState.taskBatch = event.target.value; renderWorkbenchRows(); });
    $$('[data-task-tab]').forEach(button => button.addEventListener('click', () => chooseTab(button.dataset.taskTab)));
    $('#task-show-failures')?.addEventListener('click', () => chooseTab('failed'));
    const viewRecords = () => { close(); openManagedRecords($('#progress-date').value, progressState.taskBatch || '', $('#task-batch-select').selectedOptions?.[0]?.textContent || '当前导入批次'); };
    $('#task-view-records')?.addEventListener('click', viewRecords);
    document.addEventListener('click', event => {
      if (event.target.closest('[data-task-view-records]')) viewRecords();
      const button = event.target.closest('[data-open-task-job]');
      if (button) open(button.dataset.openTaskJob);
    });
    window.addEventListener('hashchange', close);
  }
  return {initialize, open, close, syncBatches, renderSummary};
}
