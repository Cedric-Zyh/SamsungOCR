import {filenameParts, statusClass, checkStatusLabel, escapeHtml} from '../core/ui.mjs';
import {providerErrors} from '../core/provider_errors.mjs';

export function createRecordRow({ReceiptWorkbench, recordsState}) {
  function recordRow(item) {
    const fields = item.fields || {}, date = item.date_check || {};
    const providerFailed = ReceiptWorkbench.hasProviderFailure(item);
    const file = filenameParts(item.filename);
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
    const fileMeta = [order ? `订单 ${order}` : file.folder, pageInfo].filter(Boolean).join(' · ');
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
    const needsReview = item.review_status === '待复核';
    const primaryAction = providerFailed
      ? `<button type="button" class="mini-button row-review row-primary" data-retry-row="${item.id}">重试</button>`
      : `<button type="button" class="mini-button row-review ${needsReview ? 'row-primary' : 'row-secondary'}" data-open-review="${item.id}" data-review-mode="${needsReview ? 'review' : 'view'}">${needsReview ? '复核' : '查看'}</button>`;
    return `<tr class="${providerFailed ? 'failed-row' : item.review_status === '待复核' ? 'pending-row' : ''}">
      <td><input class="record-select" type="checkbox" value="${item.id}" ${recordsState.selected.has(Number(item.id)) ? 'checked' : ''} aria-label="选择 ${escapeHtml(item.filename)}"></td>
      <td class="file-cell" title="${escapeHtml(item.filename)}"><button type="button" class="record-file-link" data-open-review="${item.id}" data-review-mode="view" aria-label="查看回单 ${escapeHtml(item.filename)}"><strong>${escapeHtml(file.name)}</strong></button>${fileMeta ? `<small class="record-file-meta" title="${escapeHtml(fileMeta)}">${escapeHtml(fileMeta)}</small>` : ''}</td>
      <td class="customer-cell" title="${escapeHtml(fields['客户名称'] || '')}"><span>${escapeHtml(fields['客户名称'] || '—')}</span></td>
      <td class="signature-cell" title="仓库联系人：${escapeHtml(warehouseContact)}；仓库接收人：${escapeHtml(warehouseReceiver)}"><div><span><em>仓库联系人</em>${escapeHtml(warehouseContact || '—')}</span><small><span><em>仓库接收人</em>${escapeHtml(warehouseReceiver || '未识别')}</span>${checkLabel(signatureStatus)}</small></div></td>
      <td><div class="date-cell"><div><span><em>要求</em>${escapeHtml(expected || '—')}</span><small class="date-actual${dateDifferent ? ' date-difference' : ''}"><span><em>签收</em>${escapeHtml(date.actual || '未识别')}</span>${checkLabel(ReceiptWorkbench.checkStatus(item, 'date'))}</small></div></div></td>
      <td>${checkLabel(ReceiptWorkbench.checkStatus(item, 'seal'))}</td>
      <td class="verdict-cell" title="${escapeHtml(verdictTitle)}"><span class="pill ${statusClass(verdict)}">${escapeHtml(verdictLabel)}</span>${reviewDetail}${errors.map(message => `<small class="error-text" title="${escapeHtml(message)}">${escapeHtml(message)}</small>`).join('')}</td>
      <td><div class="row-actions">${primaryAction}<button type="button" class="mini-button row-more row-icon-action" data-more-record="${item.id}" data-more-filename="${escapeHtml(item.filename)}" aria-label="${escapeHtml(file.name)} 更多操作" aria-expanded="false" aria-controls="record-more-menu" title="更多操作"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="19" cy="12" r="1.2" fill="currentColor"/></svg></button></div></td></tr>`;
  }

  return recordRow;
}
