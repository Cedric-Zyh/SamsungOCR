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
  return check.status || '待确认';
}

export function dateAuditText(item) {
  const check = item.date_check || {};
  const source = check.source || (isDanzhengtong(check) ? providerLabel(check) : check.backend) || '日期识别';
  const lines = [`来源：${source}${check.actual ? ` · ${check.actual}` : ''}${check.confidence != null ? ` · 置信度 ${percent(check.confidence)}` : ''}`];
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
  if (rows.length && primary.message) rows.push(`<p>${escapeHtml(primary.message)}</p>`);
  return rows.join('');
}
