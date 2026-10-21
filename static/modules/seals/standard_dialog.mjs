import {escapeHtml} from '../core/ui.mjs';
import {shapeLabel, colorLabel} from './standard_view.mjs';

export function registrationEntries(sources) {
  return sources.flatMap(source => (source.candidates || []).filter(c => !c.registration_hidden)
    .map(candidate => ({source, candidate})));
}

/** Shared by receipt/sample review and the archive. All writes remain explicit. */
export function openStandardDialog({ui, standard, requirement, sources, onSave, onSaved}) {
  const {$, $$} = ui, dialog = $('#seal-standard-dialog');
  if (!dialog || dialog.open) return;
  const entries = registrationEntries(sources);
  let selected = entries.findIndex(e => e.candidate.can_register), original = false, zoom = 1, saving = false;
  if (selected < 0 && entries.length) selected = 0;
  const title = standard ? '替换标准章' : '添加标准章';
  const opener = dialog.ownerDocument.activeElement;
  dialog.innerHTML = `<header class="standard-picker-header"><div><h2 id="standard-picker-title">${title}</h2><p>签章要求：${escapeHtml(requirement || '未填写')}</p></div><button type="button" data-standard-close aria-label="关闭${title}">×</button></header>
    <div class="standard-picker-body"><aside class="standard-picker-library"><header><h3>选择印章</h3><span>${entries.length} 枚</span></header><div class="standard-picker-list">
    ${entries.map(({source, candidate:c}, index) => `<button type="button" class="standard-picker-candidate" data-picker-index="${index}" aria-pressed="false">
      <img src="${escapeHtml(c.image_url || c.original_url)}" alt="印章 ${Number(c.index)+1}" loading="lazy">
      <b>印章 ${Number(c.index)+1} · ${c.source === 'qingtong' ? '清瞳图' : '本地处理图'}</b>
      <span>${shapeLabel(c.shape)} · ${colorLabel(c.color)}</span><small>${escapeHtml(source.filename || '')}</small>
      <small>${c.can_register ? escapeHtml(c.text_status || '待核对') : '仅供查看 · 暂不可加入'}</small></button>`).join('') || '<p class="standard-picker-empty">暂无印章处理图，请重新识别后选择。</p>'}
    </div></aside><section class="standard-picker-detail"><header><div><h3>标准章预览</h3><p>核对完整章图与方向</p></div><div class="standard-picker-zoom"><button type="button" data-standard-zoom="out" aria-label="缩小印章">−</button><span data-standard-zoom-label>100%</span><button type="button" data-standard-zoom="in" aria-label="放大印章">＋</button></div></header>
      <div class="standard-picker-tabs"><button type="button" data-standard-image="processed" aria-pressed="true">处理图</button><button type="button" data-standard-image="original" aria-pressed="false">查看原图</button></div>
      <div class="standard-picker-viewport"><div class="standard-picker-image-box"><img data-standard-image-preview alt="标准章预览"></div><p data-standard-image-empty hidden>暂无可用图片</p></div>
      <div class="standard-picker-rotation"><label>顺时针旋转 <input type="number" min="-180" max="180" step="1" value="0" data-standard-rotation aria-label="标准章旋转角度"> °</label><button type="button" data-standard-turn="-90">左转 90°</button><button type="button" data-standard-turn="90">右转 90°</button><button type="button" data-standard-reset>重置</button></div>
      <div class="standard-picker-info"><p>识别文字：<strong data-standard-recognized></strong></p><p data-standard-warning></p></div>
    </section></div>
    <footer class="standard-picker-footer"><div><p>保存后可用于后续回单核验</p><p data-standard-feedback role="status" aria-live="polite"></p></div><div class="standard-picker-buttons"><button type="button" data-standard-close>取消</button><button type="button" class="primary-button" data-standard-save>${standard ? '确认替换标准章' : '加入印章中心'}</button></div></footer>`;
  const get = s => $(s, dialog), all = s => $$(s, dialog);
  const rotation = get('[data-standard-rotation]'), feedback = get('[data-standard-feedback]');
  const image = get('[data-standard-image-preview]'), box = get('.standard-picker-image-box');
  let imageFailed = false;
  function syncSave() { get('[data-standard-save]').disabled = saving || !entries[selected]?.candidate.can_register || imageFailed; }
  function transform() {
    image.style.transform = `rotate(${original ? 0 : Number(rotation.value) || 0}deg)`;
    box.style.width = `${zoom * 100}%`; box.style.height = `${zoom * 100}%`;
    get('[data-standard-zoom-label]').textContent = `${Math.round(zoom*100)}%`;
  }
  function showImage() {
    const c = entries[selected]?.candidate;
    const url = c && (original ? c.original_url : c.image_url);
    imageFailed = false; image.hidden = !url;
    get('[data-standard-image-empty]').hidden = !!url;
    if (url) image.src = url; else image.removeAttribute('src');
    all('[data-standard-image]').forEach(b => b.setAttribute('aria-pressed', String((b.dataset.standardImage === 'original') === original)));
    get('[data-standard-image="processed"]').disabled = !c?.image_url;
    get('[data-standard-image="original"]').disabled = !c?.original_url;
    all('.standard-picker-rotation button,.standard-picker-rotation input').forEach(b => b.disabled = !c?.can_register || original);
    transform(); syncSave();
  }
  function select(index) {
    selected = index; const c = entries[index]?.candidate;
    original = !c?.image_url; zoom = 1; rotation.value = '0'; feedback.textContent = '';
    all('[data-picker-index]').forEach(b => b.setAttribute('aria-pressed',String(Number(b.dataset.pickerIndex) === index)));
    get('[data-standard-recognized]').textContent = c?.recognized || '未识别到文字';
    const warning = get('[data-standard-warning]');
    warning.textContent = !c ? '' : !c.can_register ? '缺少完整章色图或形状信息，请重新识别后加入。'
      : c.text_status === '匹配' ? (c.text_match_message || '印章文字与签章要求匹配，请核对完整章图和方向。')
      : (c.text_match_message || '识别文字与签章要求尚未匹配，请核对后保存。');
    warning.dataset.tone = c?.text_status === '匹配' && c?.can_register ? 'normal' : 'warning';
    showImage();
  }
  image.onerror = () => { imageFailed = true; feedback.textContent = '图片加载失败，请重新打开或重新识别后再试。'; syncSave(); };
  function close() { if (!saving) dialog.close(); }
  const win = dialog.ownerDocument.defaultView;
  const onRoute = () => close();
  dialog.oncancel = event => { event.preventDefault(); close(); };
  dialog.onkeydown = event => {
    event.stopPropagation();
    if ((event.metaKey || event.ctrlKey) && ['s','Enter'].includes(event.key)) event.preventDefault();
  };
  dialog.onclose = () => { win.removeEventListener('hashchange', onRoute); if (opener?.isConnected) opener.focus({preventScroll:true}); };
  all('[data-standard-close]').forEach(b => b.addEventListener('click', close));
  all('[data-picker-index]').forEach(b => b.addEventListener('click', () => { if (!saving) select(Number(b.dataset.pickerIndex)); }));
  all('[data-standard-image]').forEach(b => b.addEventListener('click', () => {original=b.dataset.standardImage==='original';zoom=1;showImage();}));
  all('[data-standard-zoom]').forEach(b => b.addEventListener('click', () => {zoom=Math.max(.5,Math.min(3,zoom+(b.dataset.standardZoom==='in'?.25:-.25)));transform();}));
  rotation.addEventListener('input', transform);
  all('[data-standard-turn]').forEach(b => b.addEventListener('click', () => {rotation.value=String(((Number(rotation.value)||0)+Number(b.dataset.standardTurn)+540)%360-180);transform();}));
  get('[data-standard-reset]').addEventListener('click', () => {rotation.value='0';zoom=1;transform();});
  get('[data-standard-save]').addEventListener('click', async () => {
    const entry = entries[selected];
    if (saving || !entry?.candidate.can_register || imageFailed) return;
    if (!rotation.checkValidity()) {rotation.reportValidity();return;}
    saving = true; feedback.textContent = '正在保存标准章…';
    const controls = all('button,input').map(el => [el,el.disabled]);
    controls.forEach(([el]) => el.disabled=true);
    let response;
    try { response=await onSave({...entry,rotation:Number(rotation.value)||0}); }
    catch(error) {feedback.textContent=error.message;}
    finally {saving=false;controls.forEach(([el,disabled])=>el.disabled=disabled);syncSave();}
    if (response) {close();onSaved(response);}
  });
  select(selected); dialog.showModal(); win.addEventListener('hashchange', onRoute);
}
