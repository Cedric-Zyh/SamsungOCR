// Read-only preview of the explicit date/seal decisions sent by the review form.
// The server remains the final authority when a review is submitted.
export function normalizedReviewDate(value) {
  const parts = String(value || '').replace(/[Oo]/g, '0').match(/(20\d{2})\s*[-./年]\s*(\d{1,2})\s*[-./月]\s*(\d{1,2})/);
  if (!parts) return '';
  const date = `${parts[1]}-${parts[2].padStart(2, '0')}-${parts[3].padStart(2, '0')}`;
  const parsed = new Date(`${date}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date ? date : '';
}

export function reviewReadiness(item, draft) {
  const date = item.date_check || {}, seal = item.seal_check || {};
  const fields = item.fields || {};
  const required = normalizedReviewDate(draft.requiredDate);
  const actual = normalizedReviewDate(draft.actualDate);
  const dateChanged = String(draft.actualDate || '').trim() !== String(date.actual || '').trim();
  const requiredChanged = String(draft.requiredDate || '').trim() !== String(fields['要求到货'] || '').trim();
  const dateReliable = draft.dateConfirmed || dateChanged || date.reliable === true;
  const dateMatched = !dateChanged && !requiredChanged && !draft.dateConfirmed
    ? date.status === '匹配' : !!actual && actual === required;
  const dateReady = !!actual && dateReliable && dateMatched;
  const dateIssue = dateReady ? '' : !actual ? '签收日期尚未确认' : !required ? '请补全要求到货日期'
    : !dateMatched ? '签收日期与要求不一致' : !dateReliable ? '签收日期尚未确认' : '';

  const sealText = String(draft.sealText || '').trim();
  const sealRequirement = String(draft.sealRequirement || '').trim();
  const sealChanged = sealText !== String(seal.recognized || '').trim()
    || sealRequirement !== String(fields['签章要求'] || '').trim();
  const decision = draft.sealConfirmed;
  const sealReady = !!sealText && (decision === true
    || (decision == null && !sealChanged && seal.status === '匹配' && seal.reliable === true));
  const sealMismatch = decision === false || (decision == null && !sealChanged && seal.status === '不匹配');
  const sealPartial = decision == null && !sealChanged && seal.status === '部分匹配';
  const sealIssue = sealReady ? '' : sealMismatch ? '印章与要求不匹配'
    : !sealText ? '印章内容尚未填写' : sealPartial ? '印章文字部分匹配，需人工核对' : '印章尚未确认';
  const issues = [dateIssue && {target: 'date', message: dateIssue}, sealIssue && {target: 'seal', message: sealIssue}].filter(Boolean);
  return {ready: dateReady && sealReady, dateReady, sealReady, issues,
    dateStatus: dateReady ? '匹配' : actual && required && !dateMatched ? '不匹配' : '待确认',
    sealStatus: sealReady ? '匹配' : sealMismatch ? '不匹配' : sealPartial ? '部分匹配' : '待确认',
    message: issues.length ? issues.map(issue => issue.message).join('；') : '日期和印章已核对，可确认通过'};
}

export function reviewKeyboardAction(event) {
  if (event.defaultPrevented || event.isComposing || event.repeat) return '';
  const key = event.key.toLowerCase();
  if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey) {
    if (key === 's') return 'save';
    if (key === 'enter') return 'pass';
  }
  const editable = event.target?.closest?.('input,textarea,select,[contenteditable="true"],[contenteditable=""]');
  if (editable || event.ctrlKey || event.metaKey) return '';
  if (event.altKey && !event.shiftKey) return key === 'd' ? 'date' : key === 's' ? 'seal' : '';
  if (event.altKey) return '';
  if (['+', '='].includes(key)) return 'zoom-in';
  if (['-', '_'].includes(key)) return 'zoom-out';
  return '';
}
