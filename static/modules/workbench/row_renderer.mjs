import {filenameParts, escapeHtml} from '../core/ui.mjs';
import {workbenchAttention} from './presentation.mjs';
import {DOCUMENT_TYPES, WORK_FILTERS, overviewStatus, overviewType, selectionKey} from './overview.mjs';

/**
 * Render one workbench row.
 *
 * The workbench owns filtering and selection; this module only translates one
 * domain item into the table markup. Keeping row presentation here prevents
 * status rules and action labels from being mixed into queue orchestration.
 */
export function renderWorkbenchRow(item, {
  ReceiptWorkbench, importsState, progressState, selected, deleting, selectable
}) {
  const job = item.job;
  const record = item.record || {};
  const uploading = job && importsState.uploadingJobs?.has(job.id);
  const category = ReceiptWorkbench.category(item);
  const showActivity = uploading || (item.status === 'running' && job?.progress?.stage !== 'paused');
  let attention = workbenchAttention(item, {
    workbench: ReceiptWorkbench,
    paused: progressState.queueControl?.paused,
    uploading
  });
  const isVerdict = ['passed', 'rejected'].includes(category);
  if (isVerdict) {
    attention = {
      primary: `核验${WORK_FILTERS[category]}`,
      secondary: record.review_status || '',
      danger: category === 'rejected'
    };
  }
  const dateCheck = record.date_check || {};
  const sealCheck = record.seal_check || {};
  const expectedDate = record.fields?.['要求到货'] || dateCheck.required || '';
  const order = record.fields?.['客户订单号'] || '';

  const badge = (label, value, status) => {
    const tone = ['不匹配', '部分匹配', '部分识别'].includes(status)
      ? 'is-danger'
      : status === '匹配' ? 'is-ok' : 'is-muted';
    return `<span class="work-pass-badge ${tone}" title="${escapeHtml(`${label}：${value}（${status}）`)}"><em>${label}</em><b>${escapeHtml(value)}</b></span>`;
  };
  const checkBadges = (leading = '') => {
    const dateStatus = ReceiptWorkbench.checkStatus(record, 'date');
    const sealStatus = ReceiptWorkbench.checkStatus(record, 'seal');
    const signatureStatus = ReceiptWorkbench.checkStatus(record, 'signature');
    const displayedActual = dateCheck.actual_display || dateCheck.actual || '';
    const shortActual = dateCheck.actual && expectedDate && dateCheck.actual.slice(0, 4) === expectedDate.slice(0, 4)
      ? dateCheck.actual.slice(5)
      : displayedActual;
    const dateValue = dateCheck.actual
      ? (dateCheck.actual !== expectedDate ? `${expectedDate || '—'} → ${shortActual}` : displayedActual)
      : (displayedActual || expectedDate || '—');
    const badges = [badge('日期', dateValue, dateStatus), badge('印章', sealStatus, sealStatus)];
    if (signatureStatus !== '未识别') badges.push(badge('签名', signatureStatus, signatureStatus));
    return `<div class="work-pass-badges">${leading}${badges.join('')}</div>`;
  };
  const attentionBadge = (tone, limit = 90) => {
    const full = String(attention.primary || '');
    const firstLine = full.split('\n')[0].trim();
    const display = firstLine.length > limit ? `${firstLine.slice(0, limit)}…` : firstLine;
    const pulse = showActivity && category === 'processing' ? '<i class="status-pulse" aria-hidden="true"></i>' : '';
    return `<span class="work-attention-badge ${tone}" title="${escapeHtml(full)}">${pulse}${escapeHtml(display)}</span>`;
  };
  const metaLine = (text, danger) => text
    ? `<small class="work-pass-meta${danger ? ' is-danger' : ''}" title="${escapeHtml(text)}">${escapeHtml(text)}</small>`
    : '';
  const completionTime = isVerdict
    ? (record.updated_at || record.created_at || '').replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '')
    : '';
  const verdictMeta = completionTime
    ? `<small class="work-pass-meta" title="完成于 ${escapeHtml(completionTime)}">${escapeHtml(completionTime)}</small>`
    : '';

  let resultHtml = '';
  let noteHtml = '';
  if (isVerdict) {
    resultHtml = checkBadges();
    noteHtml = verdictMeta;
  } else if (category === 'review') {
    resultHtml = checkBadges();
    noteHtml = `${attentionBadge(attention.danger ? 'is-danger' : 'is-warning')}${metaLine(attention.secondary, attention.secondaryDanger)}`;
  } else if (category === 'failed') {
    resultHtml = `<div class="work-pass-badges">${attentionBadge('is-danger', 64)}</div>`;
    noteHtml = metaLine(attention.secondary || '可以重新尝试识别', true);
  } else {
    const pillLabel = WORK_FILTERS[overviewStatus(item, ReceiptWorkbench)] || '';
    const processingBadge = attention.primary && attention.primary !== pillLabel ? attentionBadge('is-info') : '';
    resultHtml = processingBadge ? `<div class="work-pass-badges">${processingBadge}</div>` : '';
    noteHtml = metaLine(attention.secondary, false);
  }

  let action = '';
  if (job?.status === 'failed') action = `<button class="mini-button" data-job-retry="${escapeHtml(job.id)}">重试</button>`;
  else if (category === 'failed' && item.id) action = `<button class="mini-button" data-work-retry="${escapeHtml(item.id)}">重试</button>`;
  else if (job?.status === 'ready' && job.id) action = `<button class="mini-button" data-job-start="${escapeHtml(job.id)}">开始识别</button>`;
  else if (job?.status === 'awaiting_upload' && !uploading) action = `<button class="mini-button" data-job-upload="${escapeHtml(job.id)}">补传图片</button>`;
  else if (item.id && item.status === 'succeeded') {
    const attribute = category === 'review' ? 'data-work-review' : 'data-work-view';
    action = `<button class="mini-button" ${attribute}="${escapeHtml(item.id)}">${category === 'review' ? '开始复核' : '查看'}</button>`;
  }
  const processAction = job?.id
    ? `<button class="mini-button process-button" data-process-job="${escapeHtml(job.id)}" data-process-result="${escapeHtml(item.id || '')}" data-filename="${escapeHtml(item.filename)}">流程</button>`
    : item.id ? `<button class="mini-button process-button" data-process-result="${escapeHtml(item.id)}" data-filename="${escapeHtml(item.filename)}">流程</button>` : '';
  const cancelAction = job?.id && ['awaiting_upload', 'ready', 'queued', 'running'].includes(job.status)
    ? `<button class="mini-button row-cancel" data-job-cancel="${escapeHtml(job.id)}">取消</button>` : '';
  const actionGroup = action || processAction || cancelAction
    ? `<div class="work-row-actions">${action}${processAction}${cancelAction}</div>` : '';
  const documentType = overviewType(item);
  const typeTag = ['unclassified', 'unknown'].includes(documentType)
    ? '' : `<span class="workbench-row-type">${DOCUMENT_TYPES[documentType]}</span>`;
  const customerName = record.fields?.['客户名称'] || '';
  const fileSubtitle = [customerName, order ? `订单 ${order}` : ''].filter(Boolean).join(' · ');
  const key = selectionKey(item);
  return `<tr class="work-item work-item-${category}"><td><input type="checkbox" class="workbench-row-check" data-work-select="${escapeHtml(key)}" aria-label="选择 ${escapeHtml(item.filename)}" ${selected.has(key) ? 'checked' : ''} ${!selectable(item) || deleting ? 'disabled' : ''}><span class="file-line"><strong title="${escapeHtml(item.filename)}">${escapeHtml(filenameParts(item.filename).name)}</strong>${fileSubtitle ? `<small title="${escapeHtml(fileSubtitle)}">${escapeHtml(fileSubtitle)}</small>` : ''}</span></td><td class="status-cell"><span class="workbench-row-status" data-status="${overviewStatus(item, ReceiptWorkbench)}">${WORK_FILTERS[overviewStatus(item, ReceiptWorkbench)] || '已取消'}</span>${typeTag}</td><td class="attention-cell">${resultHtml}</td><td class="note-cell">${noteHtml}</td><td>${actionGroup}</td></tr>`;
}
