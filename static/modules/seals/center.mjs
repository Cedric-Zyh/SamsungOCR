import {openStandardDialog} from './standard_dialog.mjs';
import {escapeHtml} from '../core/ui.mjs';
import {standardDetail, standardDate, shapeLabel, colorLabel} from './standard_view.mjs';
import {groupStandards} from './standard_groups.mjs';
import {bindStandardPreview} from './standard_preview.mjs';
import {recognitionInfoMarkup, recognitionInfoError, bindCorrectionSources} from './recognition_info.mjs';

const searchKey = value => String(value || '').normalize('NFKC').replace(/\s+/g, '').toLocaleLowerCase();

export function createSealCenter({ui, services, openSource}) {
  const {$, $$, toast} = ui;
  let standards = [], groups = [], selectedId = null, lastSelectedId = null, listRequest = 0, detailRequest = 0, saving = false;
  let disposePreview = () => {};
  let renderedSearch = null;
  const groupOpen = new Map();
  const root = () => $('[data-page="seal-center"]');

  function itemMarkup({standard: s, description}) {
    const title = description.branch || description.seal || (description.company ? '标准章' : description.label);
    const kind = description.branch ? description.seal : '';
    const date = standardDate(s.updated_at);
    return `<button type="button" class="seal-library-item ${s.id === selectedId ? 'is-selected' : ''}" data-standard-id="${s.id}" aria-pressed="${s.id === selectedId}" aria-label="${escapeHtml(s.requirement)}" title="${escapeHtml(s.requirement)}">
      <img src="${escapeHtml(s.image_url)}" alt="" loading="lazy"><span class="seal-library-copy"><b>${escapeHtml(title)}</b>${kind ? `<span class="seal-library-kind">${escapeHtml(kind)}</span>` : ''}<small title="更新于 ${escapeHtml(date)}">${shapeLabel(s.shape)} · ${colorLabel(s.color)}${date ? ` · ${escapeHtml(date.slice(5, 10))} 更新` : ''}</small></span><svg class="seal-library-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></button>`;
  }

  function renderList({reveal = false, expandSelection = false} = {}) {
    if (saving) return;
    const node = root(), list = $('[data-standard-list]', node);
    // Snapshot native disclosure state before replacing the DOM. Search-only
    // expansion must not overwrite the user's normal browsing choices.
    if (renderedSearch === '') $$('[data-standard-group]', node).forEach(group => groupOpen.set(group.dataset.standardGroup, group.open));
    const search = searchKey($('#seal-center-search').value);
    const visible = groups.map(group => ({...group, matches: group.items.filter(({standard}) => !search || searchKey(standard.requirement).includes(search))})).filter(group => group.matches.length);
    const matches = visible.flatMap(group => group.matches.map(item => item.standard));
    const previousId = selectedId;
    if (!matches.some(s => s.id === selectedId)) selectedId = (matches.find(s => s.id === lastSelectedId) || matches[0])?.id ?? null;
    const scrollTop = search === renderedSearch ? list.scrollTop : 0;
    renderedSearch = search;
    $('[data-standard-count]', node).textContent = String(standards.length);
    $('[data-standard-count]', node).setAttribute('aria-label', `${standards.length} 枚标准章`);
    $('[data-standard-summary]', node).textContent = search
      ? `${visible.length} 组 · ${matches.length} 枚匹配 / 共 ${standards.length} 枚`
      : `共 ${groups.length} 组 · ${standards.length} 枚`;
    $('[data-standard-list-status]', node).textContent = matches.length ? '' : search
      ? '没有找到匹配的档案，试试公司、分公司或章号。'
      : '暂无标准章。请在回单复核页选择一枚客户章加入。';
    list.innerHTML = visible.map(group => {
      const selected = group.matches.some(item => item.standard.id === selectedId);
      if (expandSelection && selected) groupOpen.set(group.key, true);
      const open = search || groupOpen.get(group.key) || false;
      const count = group.matches.length === group.items.length ? `${group.items.length} 枚` : `${group.matches.length} / ${group.items.length} 枚`;
      return `<details class="seal-library-group ${selected ? 'is-current' : ''}" data-standard-group="${escapeHtml(group.key)}" ${open ? 'open' : ''}><summary class="seal-group-heading"><span class="seal-group-name">${escapeHtml(group.title)}</span><span class="seal-group-count">${count}</span></summary><div class="seal-group-items">${group.matches.map(item => itemMarkup(item)).join('')}</div></details>`;
    }).join('');
    $$('[data-standard-group]', node).forEach(group => group.addEventListener('toggle', () => {
      if (group.isConnected && !search) groupOpen.set(group.dataset.standardGroup, group.open);
    }));
    $$('[data-standard-id]', node).forEach(button => button.addEventListener('click', () => show(Number(button.dataset.standardId))));
    list.scrollTop = scrollTop;
    if (selectedId !== null) show(selectedId, {reveal: reveal || selectedId !== previousId});
    else {
      ++detailRequest;
      disposePreview(); disposePreview = () => {};
      $('[data-standard-detail]', node).innerHTML = search
        ? '<div class="seal-standard-empty"><b>未找到匹配的标准章</b><p>修改搜索内容，或清空搜索后查看全部档案。</p></div>'
        : '<div class="seal-standard-empty"><b>每条签章要求，一枚标准章</b><p>这里展示你在复核页确认加入的印章。新识别结果不会自动进入印章中心。</p></div>';
    }
  }

  async function load() {
    const serial = ++listRequest;
    const node = root(); if (!node) return;
    $('[data-standard-list-status]', node).textContent = '正在加载…';
    let response;
    // The archive is a small complete collection. Filter it locally so searches
    // retain the parent company and the total number of independent standards.
    try { response = await services.sealStandards.list(''); }
    catch (error) {
      if (serial === listRequest) $('[data-standard-list-status]', node).textContent = '档案加载失败，请点击刷新重试。';
      throw error;
    }
    if (serial !== listRequest) return;
    standards = response.items;
    groups = groupStandards(standards);
    renderList({reveal: true});
  }
  function show(id, {reveal = false, expand = false} = {}) {
    if (saving) return;
    selectedId = id; lastSelectedId = id; const serial = ++detailRequest;
    const node = root(), detail = $('[data-standard-detail]', node);
    $$('[data-standard-id]', node).forEach(b => { const active=Number(b.dataset.standardId)===id; b.classList.toggle('is-selected',active); b.setAttribute('aria-pressed',String(active)); });
    const standard = standards.find(s => s.id === id); if (!standard) return;
    const selected = $$('[data-standard-id]', node).find(button => Number(button.dataset.standardId) === id);
    const parent = selected?.closest('[data-standard-group]');
    $$('[data-standard-group]', node).forEach(group => group.classList.toggle('is-current', group === parent));
    if (parent && expand) {parent.open = true; groupOpen.set(parent.dataset.standardGroup, true);}
    if (reveal) (parent && !parent.open ? $('summary', parent) : selected)?.scrollIntoView?.({block: 'nearest'});
    disposePreview();
    detail.innerHTML = standardDetail(standard);
    disposePreview = bindStandardPreview(detail);
    detail.scrollTop = 0;
    const infoHost = $('[data-standard-recognition-info]', detail);
    const loadInfo = async () => {
      if (!infoHost) return;
      if (typeof services.sealStandards.recognitionInfo !== 'function') {
        infoHost.innerHTML = '<p class="seal-name-load-state">识别资料暂不可用，请刷新页面。</p>';
        return;
      }
      infoHost.setAttribute('aria-busy', 'true');
      infoHost.innerHTML = '<p class="seal-name-load-state">正在加载名称词条与纠错记录…</p>';
      try {
        const data = await services.sealStandards.recognitionInfo(id);
        if (serial !== detailRequest || node.classList.contains('hidden')) return;
        if (data.standard_id !== id || data.revision !== standard.revision) {
          throw new Error('档案已更新，请刷新后查看识别资料。');
        }
        infoHost.innerHTML = recognitionInfoMarkup(data);
        bindCorrectionSources(infoHost, {ui, openSource});
      } catch (error) {
        if (serial !== detailRequest || node.classList.contains('hidden')) return;
        infoHost.innerHTML = recognitionInfoError(error.message);
        $('[data-name-info-retry]', infoHost)?.addEventListener('click', loadInfo);
      } finally {
        if (serial === detailRequest) infoHost.removeAttribute('aria-busy');
      }
    };
    loadInfo();
    const source = $('[data-standard-source]', detail);
    let sourceOpening = false;
    if (source && openSource) source.addEventListener('click', async () => {
      if (sourceOpening) return;
      sourceOpening = true; source.setAttribute('aria-busy', 'true');
      try { await openSource(standard.source_result_id, standard.source_kind); }
      catch (error) { toast(error.message || '来源回单已清理或暂时无法查看。', 'danger'); }
      finally { sourceOpening = false; source.removeAttribute('aria-busy'); }
    });
    const related = groups.find(group => group.items.some(item => item.standard.id === id))?.items.filter(item => item.standard.id !== id) || [];
    if (related.length) {
      detail.insertAdjacentHTML('beforeend', `<section class="seal-related" aria-label="同公司印章"><div class="seal-related-heading"><h3>同公司印章 <small>${related.length} 枚</small></h3><span>点击查看，章号与分公司分别核对</span></div><div class="seal-related-list">${related.map(({standard: s, description}) => `<button type="button" class="seal-related-item" data-related-standard="${s.id}" title="${escapeHtml(s.requirement)}"><img src="${escapeHtml(s.image_url)}" alt="" loading="lazy"><span class="seal-related-copy"><b>${escapeHtml(description.branch || description.seal || '公司印章')}</b><small>${escapeHtml(description.branch ? description.seal : `${shapeLabel(s.shape)} · ${colorLabel(s.color)}`)}</small></span></button>`).join('')}</div></section>`);
      $$('[data-related-standard]', detail).forEach(button => button.addEventListener('click', () => {
        if (saving) return;
        const targetId = Number(button.dataset.relatedStandard);
        const target = $$('[data-standard-id]', node).find(item => Number(item.dataset.standardId) === targetId);
        if (!target) { $('#seal-center-search').value = ''; selectedId = targetId; renderList({reveal: true, expandSelection: true}); }
        else show(targetId, {reveal: true, expand: true});
      }));
    }
    $('[data-load-standard-sources]', detail).addEventListener('click', async event => {
      const trigger = event.currentTarget;
      trigger.disabled=true; trigger.textContent='正在加载…';
      try {
        const data = await services.sealStandards.sources(id);
        if (serial !== detailRequest || node.classList.contains('hidden')) return;
        if (data.standard.revision !== standard.revision) {
          standards = standards.map(s => s.id === id ? data.standard : s);
          groups = groupStandards(standards);
          renderList({reveal: true}); toast('标准章已更新，请核对当前图片后重新选择替换章。'); return;
        }
        trigger.disabled=false; trigger.textContent='替换标准章';
        openStandardDialog({ui,standard:data.standard,requirement:data.standard.requirement,sources:data.sources,
          onSave:async ({source,candidate,rotation,recognizedOverride}) => {
            if(saving) throw new Error('正在保存，请稍候。');
            saving=true;
            try { return await services.sealStandards.save(source.id,{candidate_id:candidate.id,
              standard_revision:data.standard.revision,review_revision:source.review_revision,rotation,
              ...(recognizedOverride === undefined ? {} : {recognized_override:recognizedOverride})}); }
            finally { saving=false; }
          },
          onSaved:response => {
            standards=standards.map(s=>s.id===id?response.standard:s);
            groups = groupStandards(standards);
            renderList({reveal: true});
            load().catch(error=>toast(error.message,'danger'));
            toast('标准章已替换，图片、识别文字、形状和颜色已同步更新。');
          }
        });
      } catch(error) {toast(error.message,'danger');trigger.disabled=false;trigger.textContent='替换标准章';}
    });
  }
  function initialize(){
    const node=root();if(!node)return;
    $('#seal-center-search-form').addEventListener('submit',event=>{event.preventDefault();renderList({reveal: true});});
    const search = $('#seal-center-search');
    search.addEventListener('input', event => {if (!event.isComposing) renderList({reveal: true});});
    search.addEventListener('compositionend', () => renderList({reveal: true}));
    $('[data-standard-refresh]',node).addEventListener('click',()=>{if(!saving)load().catch(e=>toast(e.message,'danger'));});
  }
  return {initialize,load};
}
