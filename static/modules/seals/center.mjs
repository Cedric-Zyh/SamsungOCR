import {openStandardDialog} from './standard_dialog.mjs';
import {escapeHtml} from '../core/ui.mjs';
import {standardDetail, standardDate, shapeLabel, colorLabel} from './standard_view.mjs';

export function createSealCenter({ui, services}) {
  const {$, $$, toast} = ui;
  let standards = [], selectedId = null, listRequest = 0, detailRequest = 0, saving = false;
  const root = () => $('[data-page="seal-center"]');
  async function load() {
    const serial = ++listRequest;
    const node = root(); if (!node) return;
    $('[data-standard-list-status]', node).textContent = '正在加载…';
    const search = $('#seal-center-search').value.trim();
    let response;
    try { response = await services.sealStandards.list(search); }
    catch (error) {
      if (serial === listRequest) $('[data-standard-list-status]', node).textContent = '档案加载失败，请点击刷新重试。';
      throw error;
    }
    if (serial !== listRequest) return;
    standards = response.items;
    $('[data-standard-count]', node).textContent = String(standards.length);
    $('[data-standard-count]', node).setAttribute('aria-label', `${standards.length} 条签章要求`);
    $('[data-standard-list-status]', node).textContent = standards.length ? '' : search ? '没有找到匹配的档案，试试其他公司名称或签章要求。' : '暂无标准章。请在回单复核页选择一枚客户章加入。';
    $('[data-standard-list]', node).innerHTML = standards.map(s => `<button type="button" class="seal-library-item ${s.id===selectedId?'is-selected':''}" data-standard-id="${s.id}" aria-pressed="${s.id===selectedId}">
      <img src="${escapeHtml(s.image_url)}" alt="" loading="lazy"><span class="seal-library-copy"><b>${escapeHtml(s.requirement)}</b><small>${shapeLabel(s.shape)} · ${colorLabel(s.color)}</small><small>更新于 ${escapeHtml(standardDate(s.updated_at))}</small></span><svg class="seal-library-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></button>`).join('');
    $$('[data-standard-id]', node).forEach(b => b.addEventListener('click', () => show(Number(b.dataset.standardId))));
    if (standards.length) await show(standards.some(s => s.id === selectedId) ? selectedId : standards[0].id);
    else if (!standards.length) {
      selectedId = null; ++detailRequest;
      $('[data-standard-detail]', node).innerHTML = search
        ? '<div class="seal-standard-empty"><b>未找到匹配的标准章</b><p>修改搜索内容，或清空搜索后查看全部档案。</p></div>'
        : '<div class="seal-standard-empty"><b>每条签章要求，一枚标准章</b><p>这里展示你在复核页确认加入的印章。新识别结果不会自动进入印章中心。</p></div>';
    }
  }
  async function show(id) {
    if (saving) return;
    selectedId = id; const serial = ++detailRequest;
    const node = root(), detail = $('[data-standard-detail]', node);
    $$('[data-standard-id]', node).forEach(b => { const active=Number(b.dataset.standardId)===id; b.classList.toggle('is-selected',active); b.setAttribute('aria-pressed',String(active)); });
    const standard = standards.find(s => s.id === id); if (!standard) return;
    detail.innerHTML = standardDetail(standard);
    detail.scrollTop = 0;
    $('[data-load-standard-sources]', detail).addEventListener('click', async event => {
      const trigger = event.currentTarget;
      trigger.disabled=true; trigger.textContent='正在加载…';
      try {
        const data = await services.sealStandards.sources(id);
        if (serial !== detailRequest || node.classList.contains('hidden')) return;
        if (data.standard.revision !== standard.revision) {
          standards = standards.map(s => s.id === id ? data.standard : s);
          await show(id); toast('标准章已更新，请核对当前图片后重新选择替换章。'); return;
        }
        trigger.disabled=false; trigger.textContent='替换标准章';
        openStandardDialog({ui,standard:data.standard,requirement:data.standard.requirement,sources:data.sources,
          onSave:async ({source,candidate,rotation}) => {
            if(saving) throw new Error('正在保存，请稍候。');
            saving=true;
            try { return await services.sealStandards.save(source.id,{candidate_id:candidate.id,
              standard_revision:data.standard.revision,review_revision:source.review_revision,rotation}); }
            finally { saving=false; }
          },
          onSaved:response => {
            standards=standards.map(s=>s.id===id?response.standard:s);
            load().catch(error=>toast(error.message,'danger'));
            toast('标准章已替换，图片、识别文字、形状和颜色已同步更新。');
          }
        });
      } catch(error) {toast(error.message,'danger');trigger.disabled=false;trigger.textContent='替换标准章';}
    });
  }
  function initialize(){
    const node=root();if(!node)return;
    $('#seal-center-search-form').addEventListener('submit',event=>{event.preventDefault();if(!saving)load().catch(e=>toast(e.message,'danger'));});
    $('[data-standard-refresh]',node).addEventListener('click',()=>{if(!saving){selectedId=null;load().catch(e=>toast(e.message,'danger'));}});
  }
  return {initialize,load};
}
