import {standardCard} from './standard_view.mjs';
import {openStandardDialog} from './standard_dialog.mjs';

/** Keep receipt review compact; candidate selection belongs to its own dialog. */
export function createReviewStandard({ui, reviewState, services, onSaved, syncReviewReadiness, openPicker = openStandardDialog}) {
  const {$, $$, toast} = ui;
  return function renderStandard(item, content) {
    const root = $('[data-seal-standard]', content), check = item.seal_standard_check;
    if (!root || !check) return;
    const standard = check.standard;
    root.innerHTML = `<div class="seal-standard-heading"><b>标准章核对</b><a href="#seal-center">印章中心 ›</a></div>
      ${standardCard(standard)}<div class="seal-standard-entry"><p class="seal-standard-note">${standard ? '本单有一枚章的文字、形状同时符合即可。' : '首次出现的签章要求需要手动加入标准章。'}</p>
      <button type="button" class="secondary-button" data-open-standard-picker aria-haspopup="dialog" aria-controls="seal-standard-dialog">${standard ? '替换标准章' : '添加标准章'}</button></div>`;
    $('[data-open-standard-picker]', root).addEventListener('click', () => {
      if (reviewState.reviewSaving) return;
      const sampleView = !!reviewState.sampleView;
      openPicker({ui, standard, requirement:check.requirement || item.fields?.['签章要求'],
        sources:[{id:item.id,filename:item.filename,candidates:check.candidates,review_revision:item.review_revision}],
        onSave:async ({candidate,rotation}) => {
          if (reviewState.reviewSaving) throw new Error('正在保存，请稍候。');
          if (reviewState.current?.id !== item.id || !!reviewState.sampleView !== sampleView) throw new Error('当前回单已变化，请重新打开。');
          if (reviewState.reviewDirty) throw new Error('本单有未保存的修改，请先保存稍后处理，再加入或替换标准章。');
          const controls = $$('button,input,textarea,select', content).map(el => [el,el.disabled]);
          reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
          reviewState.reviewSaving = true;
          controls.forEach(([el])=>el.disabled=true);syncReviewReadiness?.();
          try {
            const save = sampleView ? services.samples.saveStandard : services.sealStandards.save;
            return await save(item.id,{candidate_id:candidate.id,standard_revision:standard?.revision||0,review_revision:item.review_revision,rotation});
          } finally {
            reviewState.reviewSaving=false;controls.forEach(([el,disabled])=>el.disabled=disabled);syncReviewReadiness?.();
          }
        },
        onSaved:response => {
          if (reviewState.current?.id !== item.id || !!reviewState.sampleView !== sampleView) return;
          onSaved(response.result);toast(standard ? '标准章已替换，图片和识别信息已同步更新。' : '已加入印章中心。');
        }
      });
    });
  };
}
