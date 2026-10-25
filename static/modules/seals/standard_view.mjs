import {escapeHtml} from '../core/ui.mjs';
import {describeStandard} from './standard_groups.mjs';

export const shapeLabel = value => ({round:'圆形', ellipse:'椭圆', rectangle:'矩形'}[value] || '未知形状');
export const colorLabel = value => ({red:'红色', blue:'蓝色'}[value] || '未知颜色');
export const standardDate = value => String(value || '').replace('T', ' ').slice(0, 16);

/** The archive gets a larger preview; the receipt review keeps its compact card. */
export function standardDetail(standard) {
  const requirement = String(standard.requirement || '未填写签章要求');
  const {company, branch, seal} = describeStandard(standard);
  const title = [company, branch].filter(Boolean).join(' · ') || requirement;
  const subtitle = seal ? `<p class="seal-detail-subtitle">${escapeHtml(seal)}</p>` : '';
  const revision = escapeHtml(standard.revision ?? 1);
  const sourceKind = standard.source_kind === 'sample' ? 'sample' : 'receipt';
  const sourceLabel = sourceKind === 'sample' ? '来源样单' : '来源回单';
  const sourceName = String(standard.source_filename || '—');
  const hasSource = Number.isInteger(Number(standard.source_result_id)) && Number(standard.source_result_id) > 0
    && (!standard.source_kind || ['receipt', 'sample'].includes(standard.source_kind));
  const source = hasSource
    ? `<button type="button" data-standard-source="${escapeHtml(standard.source_result_id)}" data-source-kind="${sourceKind}" title="${escapeHtml(sourceName)}" aria-label="查看${sourceLabel}：${escapeHtml(sourceName)}">${escapeHtml(sourceName === '—' ? `查看${sourceLabel}` : sourceName)}</button>`
    : escapeHtml(sourceName);
  return `<div class="seal-detail-eyebrow"><span>当前标准章</span></div>
    <header class="seal-detail-heading"><div><h2>${escapeHtml(title)}</h2>${subtitle}<div class="seal-detail-state"><span class="seal-standard-active"><i aria-hidden="true"></i>当前生效</span><span>第 ${revision} 版</span><time class="seal-detail-updated">${standard.updated_at ? `更新于 ${escapeHtml(standardDate(standard.updated_at))}` : '更新时间未知'}</time></div></div><div class="seal-replace-action"><button class="primary-button" type="button" data-load-standard-sources aria-haspopup="dialog" aria-controls="seal-standard-dialog">替换标准章</button><small>从同要求回单中选择</small></div></header>
    <figure class="seal-detail-preview">
      <div class="seal-preview-tools" role="group" aria-label="标准章图片缩放"><button type="button" data-seal-zoom="fit" title="恢复适应窗口">适应窗口</button><button type="button" data-seal-zoom="out" aria-label="缩小标准章图片" disabled><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button><span data-seal-zoom-label title="相对于适应窗口的缩放比例">100%</span><button type="button" data-seal-zoom="in" aria-label="放大标准章图片"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14"/></svg></button><a href="${escapeHtml(standard.image_url)}" target="_blank" rel="noopener" aria-label="查看完整标准章原图（新窗口）">查看原图</a></div>
      <div class="seal-preview-viewport" tabindex="0" role="region" aria-label="标准章图片，放大后可滚动查看"><div class="seal-preview-image"><img src="${escapeHtml(standard.image_url)}" alt="${escapeHtml(requirement)}的当前标准章"></div></div>
    </figure>
    <dl class="seal-detail-text"><div><dt>签章要求</dt><dd>${escapeHtml(requirement)}</dd></div><div><dt>识别文字</dt><dd>${escapeHtml(standard.recognized || '未识别到文字')}<small class="seal-detail-text-note">供人工核对</small></dd></div></dl>
    <dl class="seal-detail-metadata"><div><dt>印章形状</dt><dd>${shapeLabel(standard.shape)}</dd></div><div><dt>${sourceLabel}</dt><dd title="${escapeHtml(sourceName)}">${source}</dd></div><div><dt>印章颜色</dt><dd>${colorLabel(standard.color)}</dd></div><div><dt>当前版本</dt><dd>第 ${revision} 版</dd></div></dl>
    <div class="seal-detail-footer"><p>每条签章要求保留一枚标准章，分组仅便于查找，各分公司、章号独立核验。</p></div>
    `;
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
