import {escapeHtml, percent, statusPill} from './ui.mjs';

const isDanzhengtong = check => check?.recognition_mode === 'danzhengtong'
  || [check?.source, check?.backend].some(value => String(value || '').includes('单证通'));
const providerLabel = (check, variant) => check?.simulated || variant?.details?.danzhengtong?.simulated
  ? '单证通（模拟）' : '单证通';

function danzhengtongResults(item, stage) {
  const variants = (item.recognition_variants?.[stage] || []).filter(variant => variant.method === 'danzhengtong');
  if (variants.length) return variants.map(variant => ({check: variant.details?.[`${stage}_check`] || {}, variant}));
  const check = item[`${stage}_check`];
  return isDanzhengtong(check) ? [{check}] : [];
}

function evidenceStatus(check, value, error) {
  if (error || check.status === '识别失败') return '识别失败';
  if (!String(value || '').trim()) return '未识别';
  if (check.status === '匹配' && check.reliable === false) return '匹配待确认';
  return check.status || '待确认';
}

export function dateAuditText(item) {
  const check = item.date_check || {};
  const displayedActual = check.actual_display || check.actual || '';
  const source = check.source || (isDanzhengtong(check) ? providerLabel(check) : check.backend) || '日期识别';
  const lines = [`来源：${source}${displayedActual ? ` · ${displayedActual}` : ''}${check.confidence != null ? ` · 置信度 ${percent(check.confidence)}` : ''}`];
  for (const {check: provider, variant} of danzhengtongResults(item, 'date')) {
    const label = providerLabel(provider, variant);
    if (source === label && provider.actual === check.actual && !variant?.error) continue;
    const status = evidenceStatus(provider, provider.actual, variant?.error);
    lines.push(`${label}：${provider.actual || '未识别'} · ${status}`);
  }
  lines.push(...(check.rejected_candidates || []).map(row => `已拒绝 ${row.value}：${row.reason}`),
    ...(check.business_time_overridden_candidates || []).map(row => `已由跨模型共识覆盖 ${row.value}：${row.reason}`));
  return lines.join('；');
}

function evidenceRow(label, status, value, note) {
  return `<div><span>${escapeHtml(label)}</span>${statusPill(status)}<p>${escapeHtml(value)}</p><small>${escapeHtml(note)}</small></div>`;
}

const localMethodLabels = {
  paddle: 'PaddleOCR PP-OCRv5 Mobile',
  paddle_server: 'PaddleOCR PP-OCRv5 Server（大模型）',
  paddle_v6: 'PaddleOCR PP-OCRv6 Small',
  paddle_seal: 'PaddleOCR 印章专用检测模型',
};

function localProviderLabel(check, variant) {
  const backend = String(check?.backend || '').replace(/^本地\s*/, '').trim();
  return `本地 ${backend || localMethodLabels[variant?.method] || '印章 OCR'}`;
}

function localSealResults(item) {
  const variants = (item.recognition_variants?.seal || [])
    .filter(variant => !['qingtong', 'danzhengtong'].includes(variant.method))
    .map(variant => ({check: variant.details?.seal_check || {}, variant}));
  if (variants.length) return variants;
  const primary = item.seal_check || {};
  if (primary.local_channel && typeof primary.local_channel === 'object') {
    return [{check: primary.local_channel, variant: {method: 'local_channel'}}];
  }
  if (primary.dual_check || isDanzhengtong(primary)) return [];
  if (String(primary.backend || '').includes('本地') || primary.recognition_mode === 'local') {
    return [{check: primary}];
  }
  return [];
}

export function sealEvidenceMarkup(item) {
  const primary = item.seal_check || {};
  const qingtong = primary.dual_check?.selected ? primary
    : (item.recognition_variants?.seal || []).map(variant => variant.details?.seal_check).find(check => check?.dual_check?.selected);
  const dual = qingtong?.dual_check?.selected;
  const rows = [];
  if (dual) {
    const template = dual.template || {}, ocr = dual.ocr || {};
    const failed = qingtong.status === '识别失败';
    const templateStatus = template.requirement_match?.status || template.status || (template.matched ? '匹配' : '不匹配');
    const ocrStatus = ocr.comparison?.status || ocr.status || (ocr.matched ? '匹配' : '不匹配');
    rows.push(evidenceRow('清瞳 · 印章模板识别', evidenceStatus({status: templateStatus}, template.label, failed),
      template.label || '未返回模板名称', `模板名称独立对照本单签章要求 · 模板相似度 ${template.similarity == null ? '未提供' : percent(template.similarity)}（仅供参考）`));
    rows.push(evidenceRow('清瞳 · 印章文字 OCR', evidenceStatus({status: ocrStatus}, ocr.text, failed),
      ocr.text || '未返回文字结果', '独立对照本单签章要求'));
  }
  for (const {check, variant} of danzhengtongResults(item, 'seal')) {
    rows.push(evidenceRow(`${providerLabel(check, variant)} · 印章文字 OCR`, evidenceStatus(check, check.recognized, variant?.error),
      check.recognized || '未返回文字结果', '独立对照本单签章要求'));
  }
  for (const {check, variant} of localSealResults(item)) {
    const value = check.recognized || (check.all_recognized || []).join('；');
    const confidence = check.confidence == null ? '' : ` · 置信度 ${percent(check.confidence)}`;
    const note = `${check.region_source ? '清瞳印章区域内' : '本地印章区域'}处理图 OCR${confidence} · ${check.reliable === false ? '单模型辅助证据，需人工确认' : '独立对照本单签章要求'}`;
    rows.push(evidenceRow(`${localProviderLabel(check, variant)} · 印章文字 OCR`, evidenceStatus(check, value, variant?.error),
      value || '未返回文字结果', note));
  }
  if (rows.length && primary.message) rows.push(`<p>${escapeHtml(primary.message)}</p>`);
  return rows.join('');
}
