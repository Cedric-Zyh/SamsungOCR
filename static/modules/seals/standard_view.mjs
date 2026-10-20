import {escapeHtml} from '../core/ui.mjs';

export const shapeLabel = value => ({round:'圆形', ellipse:'椭圆', rectangle:'矩形'}[value] || '未知形状');
export const colorLabel = value => ({red:'红色', blue:'蓝色'}[value] || '未知颜色');
export const standardDate = value => String(value || '').replace('T', ' ').slice(0, 16);

/** The archive gets a larger preview; the receipt review keeps its compact card. */
export function standardDetail(standard) {
  const requirement = String(standard.requirement || '未填写签章要求');
  const parts = requirement.match(/^(.*公司)(收货专用章(?:[（(]\d+[）)])?)$/);
  const title = parts ? parts[1] : requirement;
  const subtitle = parts ? `<p class="seal-detail-subtitle">${escapeHtml(parts[2])}</p>` : '';
  return `<div class="seal-detail-eyebrow"><span>当前标准章</span><span class="seal-standard-active"><i aria-hidden="true"></i>使用中</span></div>
    <header class="seal-detail-heading"><div><h2>${escapeHtml(title)}</h2>${subtitle}</div><time class="seal-detail-updated">更新于 ${escapeHtml(standardDate(standard.updated_at))}</time></header>
    <figure class="seal-detail-preview">
      <a class="seal-preview-image" href="${escapeHtml(standard.image_url)}" target="_blank" rel="noopener" aria-label="查看完整标准章图片（新窗口）"><img src="${escapeHtml(standard.image_url)}" alt="${escapeHtml(requirement)}的当前标准章"></a>
      <a class="seal-icon-button seal-preview-zoom" href="${escapeHtml(standard.image_url)}" target="_blank" rel="noopener" aria-label="放大标准章图片（新窗口）" title="查看原图"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5M7.5 10.5h6M10.5 7.5v6"/></svg></a>
    </figure>
    <dl class="seal-detail-metadata"><div><dt>印章形状</dt><dd>${shapeLabel(standard.shape)}</dd></div><div><dt>来源回单</dt><dd title="${escapeHtml(standard.source_filename || '')}">${escapeHtml(standard.source_filename || '—')}</dd></div><div><dt>印章颜色</dt><dd>${colorLabel(standard.color)}</dd></div><div><dt>当前版本</dt><dd>第 ${escapeHtml(standard.revision)} 版</dd></div></dl>
    <div class="seal-detail-footer"><p>每条签章要求保留一枚标准章，替换后用于后续核验。</p><div class="seal-replace-action"><button class="primary-button" type="button" data-load-standard-sources aria-expanded="false" aria-controls="seal-standard-sources">替换标准章</button><small>从同要求回单中选择</small></div></div>
    <div id="seal-standard-sources" data-standard-sources></div>`;
}

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
    ${candidate.text_match_message ? `<small>${escapeHtml(candidate.text_match_message)}</small>` : ''}
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
