import {escapeHtml} from '../core/ui.mjs';
import {standardCard, candidateCard, selectionControls, shapeLabel, colorLabel} from './standard_view.mjs';

export function createSealCenter({ui, services}) {
  const {$, $$, toast} = ui;
  let standards = [], selectedId = null, listRequest = 0, detailRequest = 0, saving = false;
  const root = () => $('[data-page="seal-center"]');
  async function load() {
    const serial = ++listRequest;
    const node = root(); if (!node) return;
    $('[data-standard-list-status]', node).textContent = '正在加载…';
    const response = await services.sealStandards.list($('#seal-center-search').value.trim());
    if (serial !== listRequest) return;
    standards = response.items;
    $('[data-standard-count]', node).textContent = `${standards.length} 条签章要求`;
    $('[data-standard-list-status]', node).textContent = standards.length ? '' : '暂无标准章。请在回单复核页选择一枚客户章加入。';
    $('[data-standard-list]', node).innerHTML = standards.map(s => `<button type="button" class="seal-library-item ${s.id===selectedId?'is-selected':''}" data-standard-id="${s.id}" aria-pressed="${s.id===selectedId}">
      <img src="${escapeHtml(s.image_url)}" alt="标准章缩略图" loading="lazy"><span><b>${escapeHtml(s.requirement)}</b><small>${shapeLabel(s.shape)} · ${colorLabel(s.color)} · 第 ${s.revision} 版</small><small>更新于 ${escapeHtml(s.updated_at.replace('T',' ').slice(0,16))}</small></span><span aria-hidden="true">›</span></button>`).join('');
    $$('[data-standard-id]', node).forEach(b => b.addEventListener('click', () => show(Number(b.dataset.standardId))));
    if (standards.length) await show(standards.some(s => s.id === selectedId) ? selectedId : standards[0].id);
    else if (!standards.length) {
      selectedId = null; ++detailRequest;
      $('[data-standard-detail]', node).innerHTML = '<div class="seal-standard-empty"><b>每条签章要求，一枚标准章</b><p>这里展示你在复核页确认加入的印章。新识别结果不会自动进入印章中心。</p></div>';
    }
  }
  async function show(id) {
    if (saving) return;
    selectedId = id; const serial = ++detailRequest;
    const node = root(), detail = $('[data-standard-detail]', node);
    $$('[data-standard-id]', node).forEach(b => { const active=Number(b.dataset.standardId)===id; b.classList.toggle('is-selected',active); b.setAttribute('aria-pressed',String(active)); });
    const standard = standards.find(s => s.id === id); if (!standard) return;
    detail.innerHTML = `<h2>${escapeHtml(standard.requirement)}</h2>${standardCard(standard)}<p class="seal-standard-note">每条要求只保存一枚标准章。替换后，后续识别以新章的形状为准。</p><button class="mini-button" type="button" data-load-standard-sources>从同要求回单中选择替换章</button><div data-standard-sources></div>`;
    $('[data-load-standard-sources]', detail).addEventListener('click', async event => {
      event.target.disabled=true;
      try {
        const data = await services.sealStandards.sources(id);
        if (serial !== detailRequest) return;
        if (data.standard.revision !== standard.revision) {
          standards = standards.map(s => s.id === id ? data.standard : s);
          await show(id); toast('标准章已更新，请核对当前图片后重新选择替换章。'); return;
        }
        const target=$('[data-standard-sources]',detail);
        target.innerHTML=`<h3>选择替换章</h3><p>以下为相同签章要求的回单，请选择一枚完整章图。</p><div class="seal-candidate-grid">${data.sources.flatMap(s=>s.candidates.map(c=>candidateCard(c,{sourceId:s.id,filename:s.filename}))).join('') || '<p>没有可用的来源回单，请先识别新的回单。</p>'}</div>${selectionControls(true)}`;
        let selected=null, source=null;
        const selection=$('[data-standard-selection]',target), rotation=$('[data-standard-rotation]',target), feedback=$('[data-standard-feedback]',target);
        const cancel=()=>{ selected=source=null; selection.classList.add('hidden'); $$('[data-seal-candidate]',target).forEach(b=>{b.classList.remove('is-selected');b.setAttribute('aria-pressed','false');$('img',b).style.transform='';}); };
        $$('[data-seal-candidate]',target).forEach(button=>button.addEventListener('click',()=>{
          if(saving)return; cancel();source=data.sources.find(s=>s.id===Number(button.dataset.sourceId));selected=source.candidates.find(c=>c.id===button.dataset.sealCandidate);
          button.classList.add('is-selected');button.setAttribute('aria-pressed','true');rotation.value='0';feedback.textContent='';
          $('[data-standard-selected-label]',target).textContent=`${source.filename} · 印章 ${Number(selected.index)+1}`;selection.classList.remove('hidden');
        }));
        rotation.addEventListener('input',()=>{const image=$('.seal-candidate.is-selected img',target);if(image)image.style.transform=`rotate(${Number(rotation.value)||0}deg)`;});
        $('[data-standard-cancel]',target).addEventListener('click',cancel);
        $('[data-standard-save]',target).addEventListener('click',async()=>{
          if(!selected||saving)return;if(!rotation.checkValidity()){rotation.reportValidity();return;}
          saving=true;$$('button,input',target).forEach(b=>b.disabled=true);feedback.textContent='正在替换…';
          try{
            const response=await services.sealStandards.save(source.id,{candidate_id:selected.id,standard_revision:data.standard.revision,review_revision:source.review_revision,rotation:Number(rotation.value)||0});
            standards=standards.map(s=>s.id===id?response.standard:s);saving=false;
            await show(id);await load();toast('标准章已替换，图片、识别文字、形状和颜色已同步更新。');
          }catch(error){saving=false;feedback.textContent=error.message;$$('button,input',target).forEach(b=>b.disabled=false);$$('[data-seal-candidate]',target).forEach(b=>b.disabled=!data.sources.find(s=>s.id===Number(b.dataset.sourceId))?.candidates.find(c=>c.id===b.dataset.sealCandidate)?.can_register);}
        });
      } catch(error) {toast(error.message,'danger');event.target.disabled=false;}
    });
  }
  function initialize(){
    const node=root();if(!node)return;
    $('#seal-center-search-form').addEventListener('submit',event=>{event.preventDefault();if(!saving)load().catch(e=>toast(e.message,'danger'));});
    $('[data-standard-refresh]',node).addEventListener('click',()=>{if(!saving){selectedId=null;load().catch(e=>toast(e.message,'danger'));}});
  }
  return {initialize,load};
}
