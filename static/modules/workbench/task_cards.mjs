import {escapeHtml} from '../core/ui.mjs';
import {workbenchAttention} from './presentation.mjs';
import {selectionKey, overviewStatus} from './overview.mjs';

export function renderTaskEmpty(items, {filters, search = '', ReceiptWorkbench}) {
  const hasResults = items.some(item => overviewStatus(item, ReceiptWorkbench) === 'completed');
  const complete = items.length > 0 && items.every(item => overviewStatus(item, ReceiptWorkbench) === 'completed');
  const label = filters.includes('failed') ? '异常' : filters.length === 1 && filters[0] === 'ready' ? '待开始' : '进行中';
  const title = !items.length ? '这一天还没有回单' : search?.trim() ? '未找到匹配的任务' : `暂无${label}任务`;
  const description = !items.length ? '选择其他日期，或导入回单开始识别。' : search?.trim() ? '试试其他关键词，或切换任务状态。'
    : complete ? '本批次已处理完成，可前往回单列表查看结果。' : '可切换任务状态，查看其他待处理任务。';
  return `<div class="task-empty"><svg viewBox="0 0 64 64" aria-hidden="true"><rect x="9" y="7" width="29" height="37" rx="4"/><rect x="24" y="18" width="29" height="37" rx="4"/><path d="M31 28h14M31 35h9"/>${complete ? '<circle cx="50" cy="50" r="11"/><path class="task-empty-check" d="m45 50 4 4 6-7"/>' : ''}</svg><strong>${title}</strong><p>${description}</p>${hasResults ? '<button type="button" class="text-button" data-task-view-records>查看识别结果 <span aria-hidden="true">→</span></button>' : ''}</div>`;
}

export function renderTaskCard(item, {ReceiptWorkbench, importsState, progressState, selected, deleting, selectable}) {
  const job = item.job, status = overviewStatus(item, ReceiptWorkbench), key = selectionKey(item);
  const uploading = importsState.uploadingJobs?.has(job?.id);
  const busy = deleting || uploading || progressState.workPendingJobs?.has(job?.id) || !progressState.dailyReady;
  const detail = workbenchAttention(item, {workbench: ReceiptWorkbench, paused: progressState.queueControl?.paused, uploading});
  const labels = {ready: uploading ? '上传中' : item.status === 'awaiting_upload' ? '待上传' : '待开始', queued: '排队中', running: '识别中', failed: '识别失败'};
  const button = (attr, value, label, primary = false) => `<button type="button" class="mini-button${primary ? ' task-primary' : ''}" ${attr}="${escapeHtml(value)}" ${busy ? 'disabled' : ''}>${label}</button>`;
  let actions = '';
  if (job?.status === 'ready') actions = button('data-job-start', job.id, '开始识别', true);
  else if (job?.status === 'awaiting_upload') actions = button('data-job-upload', job.id, '补传图片', true);
  else if (status === 'queued') actions = button('data-job-prioritize', job.id, job.priority ? '已优先安排' : '优先处理') + button('data-job-withdraw', job.id, '取消排队');
  else if (status === 'failed') actions = job?.status === 'failed' ? button('data-job-retry', job.id, '重试', true) : button('data-work-retry', item.id, '重试', true);
  if (job && ['running', 'failed'].includes(status)) actions += `<button type="button" class="text-button" data-process-job="${escapeHtml(job.id)}" data-filename="${escapeHtml(item.filename)}">查看步骤 →</button>`;
  const details = detail.secondary ? `<p class="task-card-detail">${escapeHtml(detail.secondary)}</p>` : '';
  return `<article class="task-card task-card-${status}" data-task-job="${escapeHtml(job?.id || '')}">
    <div class="task-card-heading"><input type="checkbox" data-work-select="${escapeHtml(key)}" aria-label="选择 ${escapeHtml(item.filename)}" ${selected.has(key) ? 'checked' : ''} ${busy || !selectable(item) ? 'disabled' : ''}><strong title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</strong><span class="task-status">${labels[status] || '已结束'}</span></div>
    <p class="task-card-step">${status === 'running' && job?.progress?.stage !== 'paused' ? '<i class="status-pulse" aria-hidden="true"></i>' : ''}${escapeHtml(detail.primary)}</p>${details}
    <div class="task-card-actions">${actions}${status !== 'running' ? `<details class="task-card-more"><summary aria-label="更多任务操作">···</summary>${button('data-work-delete', key, '删除任务')}</details>` : ''}</div></article>`;
}
