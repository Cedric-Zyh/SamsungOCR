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
