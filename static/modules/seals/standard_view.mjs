import {escapeHtml} from '../core/ui.mjs';

export const shapeLabel = value => ({round:'圆形', ellipse:'椭圆', rectangle:'矩形'}[value] || '未知形状');
export const colorLabel = value => ({red:'红色', blue:'蓝色'}[value] || '未知颜色');
export function standardCard(standard) {
  if (!standard) return '<div class="seal-standard-empty"><b>尚无标准章</b><p>选择一枚转正、清理后的客户章，手动加入印章中心。</p><small>形状尚无参照，识别过程不会自动加入。</small></div>';
  return `<figure class="seal-standard-card"><a href="${escapeHtml(standard.image_url)}" target="_blank" rel="noopener" title="打开完整标准章图片"><img src="${escapeHtml(standard.image_url)}" alt="已保存的标准章"></a><figcaption><b>当前标准章</b><span>${shapeLabel(standard.shape)} · ${colorLabel(standard.color)}</span><p>${escapeHtml(standard.recognized || '未识别到文字')}</p><small>来自 ${escapeHtml(standard.source_filename)} · 第 ${standard.revision} 版</small></figcaption></figure>`;
}

export function candidateCard(candidate, {selected=false, sourceId='', filename=''} = {}) {
  const selectable = candidate.can_register;
  const status = candidate.shape_status ? `${candidate.text_status} · 形状${candidate.shape_status}` : candidate.text_status;
  return `<button type="button" class="seal-candidate ${selected ? 'is-selected' : ''}" data-seal-candidate="${escapeHtml(candidate.id)}" data-source-id="${sourceId}" aria-pressed="${selected}" ${selectable ? '' : 'disabled'}>
    <img src="${escapeHtml(candidate.image_url || candidate.original_url)}" alt="客户印章 ${Number(candidate.index) + 1}" loading="lazy">
    <span class="seal-candidate-title">印章 ${Number(candidate.index) + 1}<small>${shapeLabel(candidate.shape)} · ${colorLabel(candidate.color)}</small></span>
    <span class="seal-candidate-text">${escapeHtml(candidate.recognized || '未识别到文字')}</span>
    <small>文字${escapeHtml(status || '未识别')}${candidate.color_status === '不同' ? ' · 颜色不同（仅供核对）' : ''}${filename ? ` · ${escapeHtml(filename)}` : ''}</small>
    ${selectable ? '' : '<small>缺少完整章色图或形状信息，请重新识别后加入</small>'}
  </button>`;
}

export function selectionControls(hasStandard) {
  return `<div class="seal-standard-selection hidden" data-standard-selection>
    <p>已选择 <b data-standard-selected-label></b>。请核对完整章图与方向。</p>
    <div class="seal-standard-actions"><label>顺时针旋转 <input type="number" min="-180" max="180" step="1" value="0" data-standard-rotation aria-label="标准章旋转角度"> °</label>
    <button type="button" class="mini-button" data-standard-save>${hasStandard ? '确认替换标准章' : '加入印章中心'}</button>
    <button type="button" class="text-button" data-standard-cancel>取消选择</button></div>
    <small data-standard-feedback role="status"></small>
  </div>`;
}
