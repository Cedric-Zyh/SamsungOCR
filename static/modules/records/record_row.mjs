import {statusClass, checkStatusLabel, escapeHtml} from '../core/ui.mjs';
import {providerErrors} from '../core/provider_errors.mjs';
import {recordSelectionKey} from './selection.mjs';

export function createRecordRow({ReceiptWorkbench, recordsState}) {
  function recordRow(item) {
    if (item.processing_status) return processingRow(item);
    const fields = item.fields || {}, date = item.date_check || {};
    const providerFailed = ReceiptWorkbench.hasProviderFailure(item);
    const pageInfo = item.page_group?.page_count > 1 ? `第 ${Number(item.page_index || 0) + 1} 页 / 共 ${item.page_group.page_count} 页` : '';
    const expected = fields['要求到货'] || date.required || '';
    const warehouseReceiver = fields['仓库接收人'] || item.internal_fields?.['仓库接收人'] || '';
    const warehouseContact = fields['仓库联系人'] || item.internal_fields?.['仓库联系人'] || '';
    const signatureCheck = ReceiptWorkbench.signatureCheck(item);
    const signatureStatus = signatureCheck.status || (warehouseReceiver && warehouseContact
      ? (warehouseReceiver.replace(/\s+/g, '') === warehouseContact.replace(/\s+/g, '') ? '匹配' : '不匹配')
      : '未识别');
    const dateDifferent = date.actual && date.status === '不匹配';
    const order = fields['客户订单号'] || item.internal_fields?.['客户订单号'] || '';
    const orderLabel = order || item.filename?.split(/[\\/]/).pop() || '订单号未识别';
    const verdict = providerFailed ? '识别失败' : item.final_result || item.overall || '';
    const verdictLabel = ({'需人工复核': '待复核', '通过': '已通过'})[verdict] || verdict || '—';
    // 单元格内的核验徽章若与整行结论文案相同（如整行「识别失败」时日期列也显示「识别失败」），就不再重复渲染
    const checkLabel = status => (String(status || '') === verdict ? '' : checkStatusLabel(status));
    const redundantReview = providerFailed || item.review_status === '无需复核'
      || (verdict === '需人工复核' && item.review_status === '待复核')
      || (verdict === '通过' && item.review_status === '确认通过')
      || (verdict === '不通过' && item.review_status === '确认不通过');
    const reviewDetail = item.review_status && !redundantReview ? `<small>${escapeHtml(item.review_status)}</small>` : '';
    const verdictTitle = providerFailed ? verdict : [verdict, item.review_status].filter(Boolean).join(' · ');
    const errors = [...new Set([item.error_message, ...providerErrors(item)].filter(Boolean))];
    const primaryAction = providerFailed
      ? `<button type="button" class="mini-button row-review row-primary" data-retry-row="${item.id}">重试</button>`
      : `<button type="button" class="mini-button row-review row-secondary" data-open-review="${item.id}" data-review-mode="view">查看</button>`;
    return `<tr class="${providerFailed ? 'failed-row' : item.review_status === '待复核' ? 'pending-row' : ''}">
      <td><input class="record-select" type="checkbox" value="${item.id}" ${recordsState.selected.has(Number(item.id)) ? 'checked' : ''} aria-label="选择订单 ${escapeHtml(orderLabel)}"></td>
      <td class="file-cell" title="${escapeHtml(orderLabel)}"><button type="button" class="record-file-link" data-open-review="${item.id}" data-review-mode="view" aria-label="查看订单 ${escapeHtml(orderLabel)}"><strong>${escapeHtml(orderLabel)}</strong></button><small class="record-customer" title="${escapeHtml(fields['客户名称'] || '')}">${escapeHtml(fields['客户名称'] || '—')}</small>${pageInfo ? `<small class="record-file-meta">${escapeHtml(pageInfo)}</small>` : ''}</td>
      <td><div class="date-cell"><div><span><em>要求</em>${escapeHtml(expected || '—')}</span><small class="date-actual${dateDifferent ? ' date-difference' : ''}"><span><em>签收</em>${escapeHtml(date.actual || '未识别')}</span>${!date.actual && ReceiptWorkbench.checkStatus(item, 'date') === '未识别' ? '' : checkLabel(ReceiptWorkbench.checkStatus(item, 'date'))}</small></div></div></td>
      <td>${checkLabel(ReceiptWorkbench.checkStatus(item, 'seal'))}</td>
      <td class="signature-cell" title="仓库联系人：${escapeHtml(warehouseContact)}；仓库接收人：${escapeHtml(warehouseReceiver)}"><div><span><em>仓库联系人</em>${escapeHtml(warehouseContact || '—')}</span><small><span><em>仓库接收人</em>${escapeHtml(warehouseReceiver || '未识别')}</span>${!warehouseReceiver && signatureStatus === '未识别' ? '' : checkLabel(signatureStatus)}</small></div></td>
      <td class="verdict-cell" title="${escapeHtml(verdictTitle)}"><span class="pill ${statusClass(verdict)}">${escapeHtml(verdictLabel)}</span>${reviewDetail}${errors.map(message => `<small class="error-text" title="${escapeHtml(message)}">${escapeHtml(message)}</small>`).join('')}</td>
      <td><div class="row-actions">${primaryAction}<button type="button" class="mini-button row-more row-icon-action" data-more-record="${item.id}" data-more-filename="${escapeHtml(item.filename)}" aria-label="订单 ${escapeHtml(orderLabel)} 更多操作" aria-expanded="false" aria-controls="record-more-menu" title="更多操作"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="19" cy="12" r="1.2" fill="currentColor"/></svg></button></div></td></tr>`;
  }

  function processingRow(item) {
    const job = item.processing_job || {}, fields = item.fields || {}, status = item.processing_status;
    const label = ({awaiting_upload: '待上传', ready: '待开始', queued: '排队中', running: item.id ? '重新识别中' : '识别中', failed: '识别失败'})[status];
    const name = fields['客户订单号'] || item.filename?.split(/[\\/]/).pop() || '回单';
    const attribute = status === 'ready' ? 'data-job-start' : status === 'awaiting_upload' ? 'data-job-upload' : status === 'failed' ? 'data-job-retry' : 'data-open-task-job';
    const action = status === 'ready' ? '开始识别' : status === 'awaiting_upload' ? '补传图片' : status === 'failed' ? '重试' : '查看进度';
    const pending = status === 'failed' ? '—' : item.id ? '待重新识别' : '待识别';
    const key = recordSelectionKey(item);
    return `<tr class="processing-row" data-job-id="${escapeHtml(job.id)}">
      <td><input class="record-select" type="checkbox" value="${escapeHtml(key || '')}" ${recordsState.selected.has(key) ? 'checked' : ''} ${key === null ? 'disabled title="正在识别，请等待处理结束"' : ''} aria-label="选择 ${escapeHtml(name)}"></td>
      <td class="file-cell"><strong title="${escapeHtml(item.filename || name)}">${escapeHtml(name)}</strong><small class="record-customer" title="${escapeHtml(fields['客户名称'] || pending)}">${escapeHtml(fields['客户名称'] || pending)}</small></td>
      <td class="processing-placeholder">${pending}</td><td class="processing-placeholder">${pending}</td><td class="processing-placeholder">${pending}</td>
      <td class="verdict-cell"><span class="pill ${status === 'failed' ? 'danger' : 'pending'}">${label}</span>${status === 'failed' ? `<small class="error-text">${escapeHtml(job.error_message || '识别未完成，请重试')}</small>` : item.id ? '<small>上次结果暂不作为当前结论</small>' : ''}</td>
      <td><div class="row-actions processing-row-actions"><button type="button" class="mini-button row-secondary" data-preview-job="${escapeHtml(job.id)}" ${status === 'awaiting_upload' ? 'disabled title="图片尚未上传，请先补传图片"' : ''}>预览</button><button type="button" class="mini-button row-primary" ${attribute}="${escapeHtml(job.id)}">${action}</button>${key !== null ? `<button type="button" class="mini-button row-secondary" data-delete-job="${escapeHtml(job.id)}">删除</button>` : ''}</div></td></tr>`;
  }

  return recordRow;
}
