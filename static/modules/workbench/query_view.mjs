/** Present daily-query transitions without fetching or deciding freshness. */
export function createDailyQueryView({environment, ui, progressState, importsState,
  selected, viewState, pageSize, syncSelection, syncStartRecognition,
  renderQueueControl, renderTask, renderWorkbenchRows}) {
  const {document} = environment;
  const {$, $$} = ui;

  function loading({day, newDay}) {
    const reviewEntry = $('#open-batch-review');
    if (document.body.dataset.activePage === 'progress' && reviewEntry.dataset.reviewDay !== day) {
      reviewEntry.dataset.reviewDay = day;
      reviewEntry.setAttribute('aria-label', `人工复核，${day}，当天全部`);
      reviewEntry.title = `复核 ${day} 当天的回单`;
      reviewEntry.classList.remove('has-pending');
      $('#review-entry-count').textContent = '';
      $('#review-entry-count').classList.add('hidden');
    }
    if (newDay) {
      progressState.queueMarkup = '';
      selected.clear(); viewState.shownItems = [];
      if ($('#task-strip-counts')) $('#task-strip-counts').textContent = '正在获取任务…';
      for (const id of ['overview-completed', 'overview-total', 'overview-percent',
        'task-count-succeeded', 'task-count-failed', 'task-count-pending']) {
        if ($(`#${id}`)) $(`#${id}`).textContent = '—';
      }
      if ($('#overview-remaining')) $('#overview-remaining').textContent = '正在加载当天数据…';
      if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = '加载中';
      if ($('#workbench-type-counts')) $('#workbench-type-counts').innerHTML = '';
      $$('[data-work-count]').forEach(label => label.textContent = '—');
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
    syncStartRecognition();
    $('#progress-note').dataset.tone = '';
    $('#task-section').setAttribute('aria-busy', 'true');
  }

  function ready({counts, control}) {
    renderQueueControl();
    $('#task-section').classList.remove('hidden');
    renderTask(control.paused ? {...counts, status: control.status === 'pausing' ? '暂停中' : '已暂停'} : counts);
    syncStartRecognition();
    renderWorkbenchRows();
    $('#progress-note').textContent = importsState.batchRunning || importsState.uploadingJobs?.size ? '正在上传，请保持页面打开。' : '';
    $('#refresh-progress').textContent = '刷新';
  }

  function failed({day, newDay}) {
    syncSelection();
    if (!newDay) renderWorkbenchRows();
    if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = '更新失败';
    syncStartRecognition();
    if (newDay) { $('#task-summary').textContent = '暂时无法加载'; $('#task-summary').dataset.status = 'error'; }
    $('#progress-note').dataset.tone = 'danger';
    $('#progress-note').textContent = newDay ? `${day} 的回单加载失败，请重试。` : '更新失败，当前显示上次成功加载的回单，请重试。';
    $('#refresh-progress').textContent = '重试';
  }

  function render(state) {
    if (state.phase === 'loading') loading(state);
    else if (state.phase === 'ready') ready(state);
    else if (state.phase === 'error') failed(state);
    else if (state.phase === 'settled') $('#task-section').removeAttribute('aria-busy');
  }

  return {render};
}
