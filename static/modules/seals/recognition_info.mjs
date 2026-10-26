import {escapeHtml} from '../core/ui.mjs';

const dateLabel = value => String(value || '').replace('T', ' ').slice(0, 16) || '时间未记录';

export function highlightNameChanges(raw, corrected) {
  const a = [...String(raw || '')], b = [...String(corrected || '')];
  // Bound diff work for malformed historical records; keep all text escaped.
  if (a.length > 160 || b.length > 160) return [escapeHtml(a.join('')), escapeHtml(b.join(''))];
  const lengths = Array.from({length:a.length + 1}, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; --i) for (let j = b.length - 1; j >= 0; --j) {
    lengths[i][j] = a[i] === b[j] ? lengths[i + 1][j + 1] + 1
      : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
  }
  let i = 0, j = 0, before = '', after = '';
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      before += escapeHtml(a[i++]); after += escapeHtml(b[j++]);
    } else if (i < a.length && (j === b.length || lengths[i + 1][j] >= lengths[i][j + 1])) {
      before += `<mark class="seal-name-removed">${escapeHtml(a[i++])}</mark>`;
    } else after += `<mark class="seal-name-added">${escapeHtml(b[j++])}</mark>`;
  }
  return [before, after];
}

function correctionReason(entry) {
  return [...new Set((entry.changes || []).map(change => {
    if (change.rule === 'known_ocr_word') return '固定错词';
    if (change.rule === 'unique_registered_name') {
      const reasons = [];
      if (change.character_edits) reasons.push('名称字差校正');
      if (change.leading_noise || change.trailing_noise) reasons.push('清除首尾杂字');
      return reasons.join('、') || '已登记名称校正';
    }
    return '名称校正';
  }))].join(' · ') || '名称校正';
}

function historyItem(entry) {
  const [before, after] = highlightNameChanges(entry.raw_text, entry.corrected_text);
  const filename = String(entry.filename || '来源未记录');
  const kind = entry.source_kind === 'sample' ? 'sample' : 'receipt';
  const source = Number.isInteger(entry.result_id) && entry.result_id > 0
    ? `<button type="button" data-correction-source="${entry.result_id}" data-source-kind="${kind}" aria-label="查看${escapeHtml(filename)}的印章校正记录">${escapeHtml(filename)}<span aria-hidden="true"> ↗</span></button>`
    : `<span>${escapeHtml(filename)}</span>`;
  return `<article class="seal-correction-record"><header><div>${source}<span>${kind === 'sample' ? '样单' : '回单'}</span></div><time>${escapeHtml(dateLabel(entry.created_at))}</time></header>
    <dl><div><dt>原始识别</dt><dd>${before}</dd></div><div><dt>校正结果</dt><dd>${after}</dd></div></dl>
    <p>${escapeHtml(correctionReason(entry))}</p></article>`;
}

export function recognitionInfoMarkup(data) {
  const terms = data.terms || {}, branches = data.branches || [], history = data.history || [];
  const alternatives = terms.alternatives || [];
  const fields = [['主体名称', terms.company || '尚未确认'], ['分支／部门', terms.branch || '未单列']];
  if (!alternatives.length) fields.push(['印章用途', terms.purpose || '未注明'], ['章内编号', terms.number || '未注明']);
  if (terms.entity_type) fields.splice(1, 0, ['名称类型', terms.entity_type]);
  for (const identity of terms.identity_fields || []) fields.push([identity.label, identity.value]);
  if (terms.unclassified) fields.push(['未分类文字', terms.unclassified]);
  const status = ({parsed:'已拆分',partial:'部分拆分',unparsed:'待确认'})[terms.parse_status]
    || (terms.supported ? '已拆分' : '待确认');
  return `<section class="seal-name-info" aria-label="名称词条与纠错记录">
    <header class="seal-name-heading"><div><h3>名称词条</h3><p>按已登记要求拆分，原文保留。名称校正适用于本地 PP-OCRv6，代码独立核对。</p></div><span class="seal-name-badge">${status}</span></header>
    <dl class="seal-name-terms">${fields.map(([label,value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>
    ${alternatives.length ? `<section class="seal-name-alternatives" aria-label="分别解析的登记写法"><p class="seal-name-note">以下 ${alternatives.length} 种登记写法共享主体名称，用途、编号和代码分别核对。</p>${alternatives.map((item,index) => {
      const values = [['印章用途', item.purpose || '未注明'],['章内编号',item.number || '未注明'],
        ...(item.identity_fields || []).map(field => [field.label,field.value])];
      return `<article><h4>写法 ${index+1}</h4><dl class="seal-name-terms">${values.map(([label,value])=>`<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl></article>`;
    }).join('')}</section>` : ''}
    ${branches.length ? `<details class="seal-name-branches"><summary>同主体已登记 ${branches.length} 个分支／部门</summary><div>${branches.map(branch => `<span class="${branch === terms.branch ? 'is-current' : ''}">${escapeHtml(branch)}</span>`).join('')}</div></details>` : ''}
    ${terms.parse_status && terms.parse_status !== 'parsed' || !terms.supported ? '<p class="seal-name-note">仍有未确认的名称或文字，保留原文供核对，暂不启用这条名称的近似校正。</p>' : ''}
    <details class="seal-name-rules"><summary><span><b>纠错规则</b><small>候选唯一时校正，编号独立比对</small></span><span class="seal-name-rule-count">${(data.rules || []).length} 项</span></summary>
      <div class="seal-name-rule-list">${(data.rules || []).map(rule => `<div><span><b>${escapeHtml(rule.title)}</b><small>${escapeHtml(rule.description)}</small></span><em>${escapeHtml(rule.state)}</em></div>`).join('')}</div>
      <p class="seal-name-note">当前规则只读，与识别流程保持一致；替换标准章图片不会改变这些规则。</p></details>
    <section class="seal-name-history" aria-label="已保存的名称纠错记录"><header class="seal-name-heading"><div><h3>纠错记录</h3><p>保留原始读数，标出每次修改。</p></div>${history.length ? `<span class="seal-name-history-count">最近 ${history.length} 条${data.has_more ? ' · 仅显示最近记录' : ''}</span>` : ''}</header>
      ${history.length ? history.map(historyItem).join('') : '<div class="seal-name-history-empty"><b>暂无校正记录</b><p>新识别出现名称校正后，会在这里显示原文、校正结果和来源。已有读数不会自动改写。</p></div>'}</section>
  </section>`;
}

export function recognitionInfoError(message = '识别资料加载失败，请重试。') {
  return `<div class="seal-name-load-state" role="status"><span>${escapeHtml(message)}</span><button type="button" data-name-info-retry>重新加载</button></div>`;
}

export function bindCorrectionSources(root, {ui, openSource}) {
  let opening = false;
  for (const button of ui.$$('[data-correction-source]', root)) button.addEventListener('click', async () => {
    if (opening || !openSource) return;
    opening = true; button.setAttribute('aria-busy', 'true');
    try { await openSource(Number(button.dataset.correctionSource), button.dataset.sourceKind); }
    catch (error) { ui.toast(error.message || '来源暂时无法查看。', 'danger'); }
    finally { opening = false; button.removeAttribute('aria-busy'); }
  });
}
