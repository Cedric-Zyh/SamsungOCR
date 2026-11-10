/** A task-only surface. It never owns receipt result filters or review state. */
export function createTaskDrawer({environment, ui, progressState, renderWorkbenchRows, openManagedRecords}) {
  const {$, $$} = ui;
  const {document, window} = environment;
  let trigger;
  const tabs = {processing: ['queued', 'running'], remote: ['waiting_remote'], ready: ['ready'], failed: ['failed']};
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
      chooseTab(job.status === 'failed' ? 'failed' : ['ready', 'awaiting_upload'].includes(job.status) ? 'ready'
        : job.danzhengtong?.pending && !['running', 'queued'].includes(job.status) ? 'remote' : 'processing');
    } else {
      const jobs = [...(progressState.queueJobs?.values() || [])];
      chooseTab(jobs.some(item => ['running', 'queued'].includes(item.status)) ? 'processing'
        : jobs.some(item => item.danzhengtong?.pending) ? 'remote'
        : jobs.some(item => ['ready', 'awaiting_upload'].includes(item.status)) ? 'ready'
        : jobs.some(item => item.status === 'failed') ? 'failed' : 'ready');
    }
    if (!node.open) node.showModal();
    $('#open-task-drawer')?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('task-drawer-open');
    if (jobId) [...node.querySelectorAll('[data-task-job]')].find(item => item.dataset.taskJob === jobId)?.scrollIntoView({block: 'nearest'});
  }
  function renderSummary(items) {
    const count = rows => {
      const counts = {ready: 0, queued: 0, running: 0, waiting_remote: 0, failed: 0, succeeded: 0};
      for (const row of rows) {
        const key = row.status === 'awaiting_upload' ? 'ready' : row.status;
        if (key in counts) counts[key]++;
        if (row.job?.danzhengtong?.pending && key !== 'waiting_remote') counts.waiting_remote++;
      }
      return counts;
    };
    const all = count(items), current = all;
    if ($('#task-strip-scope')) $('#task-strip-scope').textContent = `${$('#progress-date').value} · 当天全部回单`;
    if ($('#workbench-state-pill') && progressState.dailyReady) $('#workbench-state-pill').textContent = progressState.queueControl?.paused ? (progressState.queueControl.status === 'pausing' ? '暂停中' : '已暂停') : all.running ? '正在识别' : all.queued ? '排队中' : all.ready ? '等待开始' : items.length ? '常规识别已完成' : '暂无任务';
    const activeSummary = [[all.ready, '待开始'], [all.queued, '排队'], [all.running, '识别中'], [all.failed, '失败']]
      .filter(([value]) => value).map(([value, label]) => `${label} ${value}`).join(' · ');
    if ($('#task-strip-counts')) $('#task-strip-counts').textContent = items.length
      ? [`已完成 ${all.succeeded + all.failed} / ${items.length} 张`, activeSummary].filter(Boolean).join(' · ')
      : '导入回单后开始识别';
    if ($('#task-drawer-day')) $('#task-drawer-day').textContent = $('#progress-date').value;
    for (const [id, value] of Object.entries({'task-count-succeeded': current.succeeded, 'task-count-failed': current.failed, 'task-count-pending': current.ready + current.queued + current.running})) {
      if ($(`#${id}`)) $(`#${id}`).textContent = value.toLocaleString();
    }
    if ($('#task-batch-title')) $('#task-batch-title').textContent = `当天全部回单 · ${items.length} 张`;
    if ($('#overview-remaining')) $('#overview-remaining').textContent = `成功 ${current.succeeded} · 失败 ${current.failed} · 识别中 ${current.running} · 排队 ${current.queued} · 待开始 ${current.ready}`;
    if ($('#task-failure-notice')) {
      $('#task-failure-notice').classList.toggle('hidden', !current.failed);
      $('#task-failure-notice span').textContent = `${current.failed} 张识别失败，可查看原因并重试`;
    }
    const filters = progressState.workFilters || [];
    $$('[data-task-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.taskTab === (filters.includes('waiting_remote') ? 'remote' : filters.includes('failed') ? 'failed' : filters.length === 1 && filters[0] === 'ready' ? 'ready' : 'processing'))));
    const processing = $('[data-work-count="processing"]');
    if (processing) processing.textContent = current.queued + current.running;
    const remote = $('[data-work-count="waiting_remote"]');
    if (remote) remote.textContent = current.waiting_remote;
  }
  function initialize() {
    $('#open-task-drawer')?.addEventListener('click', () => open());
    $('#close-task-drawer')?.addEventListener('click', close);
    const node = $('#task-drawer');
    node?.addEventListener('close', () => {
      document.body.classList.remove('task-drawer-open');
      $('#open-task-drawer')?.setAttribute('aria-expanded', 'false');
      if (trigger?.isConnected) trigger.focus({preventScroll: true});
      else $('#open-task-drawer')?.focus({preventScroll: true});
    });
    node?.addEventListener('click', event => {
      if (event.target !== node) return;
      const rect = node.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
    });
    $$('[data-task-tab]').forEach(button => button.addEventListener('click', () => chooseTab(button.dataset.taskTab)));
    $('#task-show-failures')?.addEventListener('click', () => chooseTab('failed'));
    const viewRecords = () => { close(); openManagedRecords($('#progress-date').value); };
    $('#task-view-records')?.addEventListener('click', viewRecords);
    document.addEventListener('click', event => {
      if (event.target.closest('[data-task-view-records]')) viewRecords();
      const button = event.target.closest('[data-open-task-job]');
      if (button) open(button.dataset.openTaskJob);
    });
    window.addEventListener('hashchange', close);
  }
  return {initialize, open, close, renderSummary};
}
