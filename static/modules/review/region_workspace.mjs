import {escapeHtml, statusPill} from '../core/ui.mjs';
import {sealImageUrl} from './seal_workspace.mjs';

const objects = value => Array.isArray(value) ? value.filter(row => row && typeof row === 'object') : [];
const text = value => typeof value === 'string' ? value.trim() : '';
const readings = variants => [...new Set(objects(variants).flatMap(row => row.ocr_texts || []).map(text).filter(Boolean))].join(' / ');
const button = (label, url, caption = '查看图片') => sealImageUrl(url)
  ? `<button type="button" class="text-button" data-region-evidence-url="${escapeHtml(url)}" data-region-evidence-label="${escapeHtml(label)}" aria-label="查看${escapeHtml(label)}">${caption} ›</button>` : '';

export function dateWorkspaceModel(record) {
  const artifacts = objects(record.processing_artifacts?.date);
  const item = artifacts.find(row => row.variant === '日期区域') || artifacts.find(row => row.variant === '紧凑区域') || artifacts[0] || {};
  const lineVariants = [...objects(item.date_line_ocr_variants), ...objects(item.date_line_display_ocr_variants)];
  const definitions = [
    ['日期行原图', item.date_line_original_url, '日期行原图', lineVariants],
    ['日期行去印章色', item.date_line_color_clean_url, '日期行去印章色', lineVariants],
    ['日期行去外框', item.date_line_positioned_frame_clean_url, '日期行去外框', lineVariants],
    ['日期行去表格线', item.date_line_table_clean_url, '日期行去表格线', lineVariants],
    ['日期区域原图', item.original_url, '原始裁剪', item.ocr_variants],
    ['日期区域去印章色', item.color_clean_url, '去印章色', item.ocr_variants],
    ['日期区域去表格线', item.line_clean_url, '去表格线', item.ocr_variants],
  ];
  const seen = new Set();
  const steps = definitions.filter(([, url]) => sealImageUrl(url) && !seen.has(url) && seen.add(url))
    .map(([label, url, preprocessing, variants]) => {
      const rows = objects(variants).filter(row => row.preprocessing === preprocessing);
      return {label, url, text: readings(rows), note: rows.map(row => row.recognition_scope).filter(Boolean).join('；')};
    });
  const check = record.date_check || {};
  const original = sealImageUrl(item.date_line_original_url || item.original_url);
  const processed = sealImageUrl(item.date_line_positioned_frame_clean_url || item.date_line_color_clean_url || item.color_clean_url || item.line_clean_url);
  const rawReadings = readings(lineVariants) || readings(item.ocr_variants);
  return {group: 'date', label: '日期区域', original, processed, steps,
    result: text(check.actual_display || check.actual), required: text(record.fields?.['要求到货'] || check.required),
    status: check.status === '匹配' && !check.reliable ? '匹配待确认' : check.status || '未识别',
    rawReadings, message: text(check.message),
    history: artifacts.map(row => ({label: row.variant || '日期区域', variants: [
      ...objects(row.date_line_ocr_variants), ...objects(row.date_line_display_ocr_variants), ...objects(row.ocr_variants),
    ]})),
  };
}

export function requirementWorkspaceModel(record) {
  const artifacts = objects(record.processing_artifacts?.signature_requirement);
  const item = artifacts.find(row => row.ocr_scope === 'clean_page') || artifacts[0] || {};
  const steps = artifacts.flatMap((row, index) => [
    {label: '签章要求行原图', url: row.original_url, text: '', note: '原图仅供查看', source: artifacts.length > 1 ? `记录 ${index + 1}` : ''},
    {label: row.ocr_scope === 'clean_page' ? '去印章色整页识别输入（局部）' : '签章要求行去印章色图（识别输入）',
      url: row.color_clean_url, text: text(row.ocr_text), error: text(row.error),
      source: row.ocr_scope === 'clean_page' ? '整页识别结果' : '二次识别 OCR'},
  ]).filter(row => sealImageUrl(row.url));
  const original = sealImageUrl(item.original_url), processed = sealImageUrl(item.color_clean_url);
  const [width, height] = item.page_size || [], box = item.crop_box;
  const location = Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)
    && width > 0 && height > 0 && box[0] >= 0 && box[1] >= 0 && box[2] <= width && box[3] <= height
    && box[2] > box[0] && box[3] > box[1] ? [box[0] / width, box[1] / height, (box[2] - box[0]) / width, (box[3] - box[1]) / height] : null;
  const rawReadings = [...new Set(artifacts.map(row => text(row.ocr_text)).filter(Boolean))].join(' / ');
  return {group: 'requirement', label: '签章要求', original, processed, steps,
    result: text(record.fields?.['签章要求']), rawReadings,
    source: text(record.field_metadata?.['签章要求']?.source) || (item.ocr_scope === 'clean_page' ? '整页识别结果' : ''),
    pageUrl: location ? sealImageUrl(record.preview_url) : '', location,
  };
}

export function renderRegionWorkspace(model) {
  const date = model.group === 'date';
  const comparison = date ? `<div class="region-date-values"><div><span>要求到货日期</span><strong>${escapeHtml(model.required || '未提供')}</strong></div><div><span>识别签收日期</span><strong>${escapeHtml(model.result || '未识别')}</strong></div></div>` : '';
  const history = model.history?.some(row => row.variants.length) ? `<details class="seal-raw-record"><summary>全部识别读数</summary>${model.history.map(item => `<div class="region-read-history"><strong>${escapeHtml(item.label)}</strong>${item.variants.map(row => `<p><span>${escapeHtml(row.preprocessing || row.method || '识别读数')}</span><b>${escapeHtml(readings([row]) || row.recognition_scope || '未识别')}</b></p>`).join('')}</div>`).join('')}</details>` : '';
  return `<section class="seal-workspace region-workspace"><header class="seal-result-heading"><h4>${date ? '日期识别结果' : '签章要求'}</h4>${date ? statusPill(model.status) : '<span class="seal-adoption">当前文字</span>'}</header>
    <div class="seal-final-text">${escapeHtml(model.result || (date ? '未识别到完整日期' : '尚无签章要求文字'))}</div>${comparison}
    ${model.rawReadings ? `<div class="region-raw-reading"><span>原始读数</span><p>${escapeHtml(model.rawReadings)}</p></div>` : ''}
    ${model.source ? `<p class="region-source">来源：${escapeHtml(model.source)}</p>` : ''}
    ${model.message && date ? `<p class="region-source">${escapeHtml(model.message)}</p>` : ''}
    <div class="seal-image-entry">${button(`${model.label}原图`, model.original || model.processed, '查看区域图片')}${model.pageUrl ? '<button type="button" class="text-button" data-region-location>查看原图位置 ›</button>' : ''}<small>在左侧查看，可放大与拖动</small></div>
    <details class="seal-recognition-process"><summary>识别过程<span>处理图片与原始读数</span></summary><div class="region-processing-steps">${model.steps.map((step, index) => `<div class="region-processing-step"><span>${index + 1}</span><div><strong>${escapeHtml(step.label)}</strong>${step.source ? `<small>${escapeHtml(step.source)}</small>` : ''}<p>${escapeHtml(step.error ? `识别失败：${step.error}` : step.text || step.note || '未保存识别读数')}</p></div>${button(step.label, step.url)}</div>`).join('') || '<p class="region-source">这条记录未保存区域处理图片。</p>'}</div>${history}</details></section>`;
}

export function bindRegionWorkspace(root, model, previewRegion) {
  if (!root?.querySelectorAll) return;
  const show = (url, label, location = null, evidenceSelected = false) => previewRegion?.({...model, url, evidenceLabel: label, location, evidenceSelected});
  root.querySelectorAll('[data-region-evidence-url]').forEach(button => button.addEventListener('click', () => {
    root.querySelectorAll('[data-region-evidence-url]').forEach(node => node.setAttribute('aria-pressed', String(node === button)));
    show(button.dataset.regionEvidenceUrl, button.dataset.regionEvidenceLabel, null, true);
  }));
  root.querySelector('[data-region-location]')?.addEventListener('click', () => show(model.pageUrl, '整页定位', model.location));
  return () => show(model.original || model.processed, model.original ? `${model.label}原图` : '处理后');
}
