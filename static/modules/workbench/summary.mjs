/** Task counters and customer overview rendering. */
import {localToday} from '../core/ui.mjs';
import {overviewCounts} from './overview.mjs';

export function createWorkbenchSummary({environment, ui, progressState, ReceiptWorkbench}) {
  const {document} = environment;
  const {$} = ui;
  function renderTask(task) {
    const percent = task.total ? Math.min(100, Math.round(task.completed / task.total * 100)) : 0;
    const finished = task.total > 0 && task.completed >= task.total;
    const summary = !task.total ? '暂无回单' : `已处理 ${task.completed} / ${task.total} 张${task.ready ? ` · ${task.ready} 张待开始` : finished ? ' · 识别已结束' : ''}`;
    $('#task-summary').textContent = `${summary}${task.error_message ? ` · ${task.error_message}` : ''}`;
    $('#task-progress').style.width = `${percent}%`;
    $('#task-progress').parentElement.setAttribute('aria-valuenow', percent);
    $('#task-summary').dataset.status = finished && task.pending_review ? 'review' : finished ? 'done' : 'running';
    // A workbench request may finish after navigation; it must not replace the
    // shared entry's current-filter scope on the records page.
    if (document.body.dataset.activePage !== 'progress') return;
    const count = Number(task.pending_review) || 0;
    $('#review-entry-count').textContent = count;
    $('#review-entry-count').classList.toggle('hidden', count === 0);
    $('#open-batch-review').classList.toggle('has-pending', count > 0);
    const scopeDay = $('#progress-date').value || localToday();
    $('#open-batch-review').dataset.reviewDay = scopeDay;
    $('#open-batch-review').setAttribute('aria-label', `人工复核，${scopeDay}，${count} 张待复核`);
    $('#open-batch-review').title = count ? `复核 ${scopeDay} 当天的待复核回单` : `${scopeDay} 当天暂无待复核回单`;
  }

  function renderOverview(items) {
    const counts = overviewCounts(items, ReceiptWorkbench);
    const percent = counts.total ? Math.round(counts.processed / counts.total * 1000) / 10 : 0;
    for (const [id, value] of Object.entries({'overview-completed': counts.processed.toLocaleString(), 'overview-total': counts.total.toLocaleString(), 'overview-percent': `${percent}%`})) {
      if ($(`#${id}`)) $(`#${id}`).textContent = value;
    }
    $('#task-progress').style.width = `${percent}%`;
    $('#task-progress').parentElement.setAttribute('aria-valuenow', percent);
    if ($('#overview-remaining')) $('#overview-remaining').textContent = `剩余 ${counts.ready + counts.queued + counts.running} 张 · 待开始 ${counts.ready} 张 · 排队中 ${counts.queued} 张 · 识别中 ${counts.running} 张`;
    if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = !counts.total ? '暂无单据' : progressState.queueControl?.paused ? (progressState.queueControl.status === 'pausing' ? '暂停中' : '已暂停') : counts.running ? '正在识别' : counts.ready + counts.queued ? '等待处理' : '识别已结束';

  }

  return {renderTask, renderOverview};
}
