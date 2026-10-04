/** Form serialization and invariant checks for a review submission. */
export function collectReviewPayload(form, reviewStatus, finalResult, revision, queryAll = selector => form.querySelectorAll(selector)) {
  const fields = {};
  [...queryAll('[name^="field:"]')].forEach(input => {
    fields[input.name.slice(6)] = input.value;
  });
  const productRows = [...queryAll('.product-cell-input')].map(input => ({
    row: Number(input.dataset.productRow), column: input.dataset.productColumn, value: input.value
  }));
  const saveGroundTruth = form.save_ground_truth.checked;
  const truthValue = form.truth_seal_should_match.value;
  const payload = {
    fields, product_rows: productRows, actual_date: form.actual_date.value,
    seal_text: form.seal_text.value, error_type: form.error_type.value,
    human_note: form.human_note.value, review_status: reviewStatus,
    final_result: finalResult, action: reviewStatus, save_ground_truth: saveGroundTruth,
    truth_seal_should_match: truthValue === '' ? null : truthValue === 'true',
    review_revision: revision,
    actual_date_confirmed: form.actual_date_confirmed.value === 'true',
    seal_confirmed_match: form.seal_confirmed_match.value === '' ? null : form.seal_confirmed_match.value === 'true',
    signature_confirmed_match: form.signature_confirmed_match
      ? (form.signature_confirmed_match.value === '' ? null : form.signature_confirmed_match.value === 'true')
      : null,
  };
  return {payload, saveGroundTruth, truthValue};
}

export function reviewSubmissionError({saveGroundTruth, truthValue, reviewStatus, ready, readinessMessage}) {
  if (saveGroundTruth && reviewStatus === '待复核') return '评测真值只能在确认通过或确认不通过时保存';
  if (saveGroundTruth && !['true', 'false'].includes(truthValue)) return '请人工选择印章真值结论';
  if (reviewStatus === '确认通过' && !ready) return readinessMessage || '当前信息未达到通过条件';
  return '';
}

/** Save lifecycle owns duplicate prevention, conflicts and post-save navigation. */
export function createReviewSubmission({ui, reviewState, services, currentReadiness,
  syncReviewReadiness, markReviewSaved, continueReview, refreshVisibleResults}) {
  const {$, $$, toast} = ui;
  async function submitReview(reviewStatus, finalResult) {
    if (reviewState.reviewSaving) return;
    const form = $('#review-form');
    const {payload, saveGroundTruth, truthValue} = collectReviewPayload(
      form, reviewStatus, finalResult, reviewState.current.review_revision,
      selector => $$(selector, form)
    );
    const readiness = currentReadiness();
    const validationError = reviewSubmissionError({
      saveGroundTruth, truthValue, reviewStatus, ready: readiness.ready,
      readinessMessage: readiness.message
    });
    if (validationError) return toast(validationError, 'warning');
    const id = reviewState.current.id;
    reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
    const controls = $$('button,input,textarea,select', form).map(el => [el, el.disabled]);
    reviewState.reviewSaving = true; controls.forEach(([el]) => el.disabled = true);
    syncReviewReadiness();
    $('[data-save-state]').textContent = '正在保存…';
    let saved = false;
    try {
      reviewState.current = await (services.records.review(id, payload));
      reviewState.reviewDirty = false; saved = true;
      markReviewSaved(id, reviewStatus);
      toast(reviewState.current.ground_truth_saved ? '复核与样单标注已保存' : '复核结果已保存');
    } catch (error) {
      const conflict = error.code === 'review_revision_conflict';
      $('[data-save-state]').textContent = conflict ? '记录已更新，当前编辑已保留' : '保存失败，修改已保留';
      toast(conflict ? `${error.message}。当前编辑已保留，请重新打开回单后核对。` : error.message, 'danger');
    } finally {
      reviewState.reviewSaving = false; controls.forEach(([el, disabled]) => el.disabled = disabled);
      syncReviewReadiness();
    }
    if (saved) {
      $('[data-save-state]').textContent = '已保存'; $('[data-save-state]').classList.remove('unsaved');
      await continueReview().catch(error => toast(`已保存，下一张加载失败：${error.message}`, 'danger'));
      refreshVisibleResults().catch(() => toast('已保存，列表暂未刷新，可稍后刷新查看'));
    }
  }

  return {submitReview};
}
