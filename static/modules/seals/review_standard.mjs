import {standardCard, candidateCard, selectionControls} from './standard_view.mjs';

/** Form-local controls; selecting a stamp never creates a standard by itself. */
export function createReviewStandard({ui, reviewState, services, onSaved, syncReviewReadiness}) {
  const {$, $$, toast} = ui;
  return function renderStandard(item, content, mode) {
    const root = $('[data-seal-standard]', content), check = item.seal_standard_check;
    if (!root || !check) return;
    const standard = check.standard;
    root.innerHTML = `<div class="seal-standard-heading"><b>标准章核对</b><a href="#seal-center">印章中心 ›</a></div>
      ${standardCard(standard)}
      <p class="seal-standard-note">${standard ? '本单有一枚章的文字、形状同时符合即可。颜色差异仅供核对。' : '首次出现的签章要求需要手动加入标准章。'}</p>
      <div class="seal-candidate-grid">${check.candidates.map(c => candidateCard(c)).join('') || '<p class="empty-state">暂无单枚印章处理图，请重新识别后选择。</p>'}</div>
      ${mode === 'view' ? '<small>进入复核后可加入或替换标准章。</small>' : selectionControls(!!standard)}`;
    if (mode === 'view') { $$('[data-seal-candidate]', root).forEach(b => b.disabled = true); return; }
    let selected = null;
    const selection = $('[data-standard-selection]', root);
    const rotation = $('[data-standard-rotation]', root);
    const feedback = $('[data-standard-feedback]', root);
    function reset() {
      selected = null; selection.classList.add('hidden');
      $$('[data-seal-candidate]', root).forEach(b => { b.classList.remove('is-selected'); b.setAttribute('aria-pressed','false'); $('img',b).style.transform=''; });
    }
    $$('[data-seal-candidate]', root).forEach(button => button.addEventListener('click', () => {
      if (reviewState.reviewSaving) return;
      reset(); selected = check.candidates.find(c => c.id === button.dataset.sealCandidate);
      button.classList.add('is-selected'); button.setAttribute('aria-pressed','true');
      rotation.value = '0'; feedback.textContent = '';
      $('[data-standard-selected-label]', root).textContent = `印章 ${Number(selected.index)+1}`;
      selection.classList.remove('hidden');
    }));
    rotation.addEventListener('input', event => {
      event.stopPropagation();
      const image = $('.seal-candidate.is-selected img', root);
      if (image) image.style.transform = `rotate(${Number(rotation.value) || 0}deg)`;
    });
    rotation.addEventListener('change', event => event.stopPropagation());
    $('[data-standard-cancel]', root).addEventListener('click', reset);
    $('[data-standard-save]', root).addEventListener('click', async () => {
      if (!selected || reviewState.reviewSaving) return;
      if (reviewState.reviewDirty) {
        feedback.textContent = '本单有未保存的修改，请先保存稍后处理，再加入或替换标准章。'; return;
      }
      if (!rotation.checkValidity()) { rotation.reportValidity(); return; }
      const controls = $$('button,input,textarea,select', content).map(el => [el, el.disabled]);
      reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
      reviewState.reviewSaving = true;
      controls.forEach(([el]) => el.disabled = true);
      syncReviewReadiness?.();
      feedback.textContent = '正在保存标准章…';
      try {
        const response = await services.sealStandards.save(item.id, {
          candidate_id:selected.id, standard_revision:standard?.revision || 0,
          review_revision:item.review_revision, rotation:Number(rotation.value) || 0,
        });
        reviewState.reviewSaving = false;
        if (reviewState.current?.id !== item.id) return;
        onSaved(response.result);
        toast(standard ? '标准章已替换，图片和识别信息已同步更新。' : '已加入印章中心。');
      } catch (error) {
        feedback.textContent = error.message;
      } finally {
        reviewState.reviewSaving = false;
        controls.forEach(([el, disabled]) => el.disabled = disabled);
        syncReviewReadiness?.();
      }
    });
  };
}
