import {escapeHtml} from '../core/ui.mjs';
import {workbenchAttention, workbenchIssues} from './presentation.mjs';
import {WORK_FILTERS, overviewStatus, selectionKey} from './overview.mjs';

/** Queue controls describe processing; completed rows summarize the saved verdict. */
export function renderWorkbenchRow(item, {ReceiptWorkbench, importsState, progressState, selected, deleting, selectable}) {
  const job = item.job, status = overviewStatus(item, ReceiptWorkbench), key = selectionKey(item);
  const uploading = job && importsState.uploadingJobs?.has(job.id);
  const busy = deleting || uploading || progressState.workPendingJobs?.has(job?.id) || !progressState.dailyReady;
  let detail = workbenchAttention(item, {workbench: ReceiptWorkbench, paused: progressState.queueControl?.paused, uploading});
  const outcome = status === 'completed' ? ReceiptWorkbench.category(item) : '';
  if (outcome) {
    const record = item.record || item;
    const issues = workbenchIssues(record, ReceiptWorkbench);
    const reasons = issues.slice(0, 2).map(issue => issue.message).join(' · ')
      + (issues.length > 2 ? ` · 另有 ${issues.length - 2} 项待确认` : '');
    detail = {
      primary: ({passed: '已通过', review: '待复核', rejected: '不通过'})[outcome] || '识别完成',
      secondary: outcome === 'review' ? reasons || '请查看识别结果并确认'
        : ['确认通过', '确认不通过'].includes(record.review_status) ? '人工复核已确认' : '可查看日期、印章和签名结果',
    };
  }
  if (status === 'queued' && job?.priority) detail = {...detail, secondary: '已优先安排 · 当前任务结束后处理'};
  const button = (attribute, value, label, primary = false) => `<button type="button" class="mini-button${primary ? ' queue-primary' : ''}" ${attribute}="${escapeHtml(value)}" ${busy ? 'disabled' : ''}>${label}</button>`;
  let actions = '';
  if (job?.status === 'ready') actions = button('data-job-start', job.id, '开始识别', true);
  else if (job?.status === 'awaiting_upload') actions = button('data-job-upload', job.id, '补传图片', true);
  else if (status === 'queued') actions = button('data-job-prioritize', job.id, '优先处理') + button('data-job-withdraw', job.id, '取消排队');
  else if (status === 'running') actions = `<button type="button" class="mini-button" data-process-job="${escapeHtml(job.id)}" data-filename="${escapeHtml(item.filename)}">查看进度 →</button>`;
  else if (status === 'failed') actions = job?.status === 'failed' ? button('data-job-retry', job.id, '重试', true) : item.id ? button('data-work-retry', item.id, '重试', true) : '';
  else if (item.id) actions = button('data-work-view', item.id, '查看结果 →');
  if (!['running', 'cancelled'].includes(status)) actions += `<button type="button" class="mini-button queue-delete" data-work-delete="${escapeHtml(key)}" ${busy ? 'disabled' : ''}>删除</button>`;
  const subtitle = item.record?.fields?.['客户订单号'] ? `订单 ${item.record.fields['客户订单号']}` : job?.created_at ? `导入于 ${job.created_at.replace('T', ' ').slice(0, 16)}` : '';
  return `<tr class="work-item work-item-${status}${selected.has(key) ? ' is-selected' : ''}">
    <td class="queue-check"><input type="checkbox" data-work-select="${escapeHtml(key)}" aria-label="选择 ${escapeHtml(item.filename)}" ${selected.has(key) ? 'checked' : ''} ${!selectable(item) || busy ? 'disabled' : ''}></td>
    <td><span class="file-line"><strong title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</strong><small>${escapeHtml(subtitle)}</small></span></td>
    <td><span class="queue-status" data-status="${status}">${uploading ? '上传中' : WORK_FILTERS[status]}</span></td>
    <td class="queue-step"${outcome ? ` data-outcome="${escapeHtml(outcome)}"` : ''}><span title="${escapeHtml(detail.primary)}">${status === 'running' && job?.progress?.stage !== 'paused' ? '<i class="status-pulse" aria-hidden="true"></i>' : ''}${escapeHtml(detail.primary)}</span>${detail.secondary ? `<small title="${escapeHtml(detail.secondary)}">${escapeHtml(detail.secondary)}</small>` : ''}</td>
    <td><div class="work-row-actions">${actions}</div></td></tr>`;
}
