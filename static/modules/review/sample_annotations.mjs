/** Sample labels never overwrite the machine evidence used by comparisons. */
import {normalizedReviewDate} from './review_readiness.mjs';
const fieldNames = {truth_customer: '客户名称', truth_requirement: '签章要求', truth_required_date: '要求到货'};

export function syncSampleDate(ui, root) {
  const actual = ui.$('[name=truth_actual_date]', root);
  actual.disabled = ui.$('[name=truth_date_present]', root).value !== 'true';
}

export function renderSampleAnnotations(ui, reviewState, truth, root) {
  const {$} = ui, entry = truth.entry || {};
  reviewState.sampleAnnotationRevision = truth.revision;
  for (const [name, field] of Object.entries(fieldNames)) $(
    `[name=${name}]`, root).value = entry.fields?.[field] || '';
  $('[name=truth_date_present]', root).value = typeof entry.date_present === 'boolean'
    ? String(entry.date_present) : entry.actual_date ? 'true' : '';
  $('[name=truth_actual_date]', root).value = normalizedReviewDate(entry.actual_date);
  $('[name=truth_seal_should_match]', root).value = typeof entry.seal_should_match === 'boolean'
    ? String(entry.seal_should_match) : '';
  for (const key of ['human_note', 'error_type']) {
    const input = $(`[name=${key}]`, root);
    const value = entry[key] ?? reviewState.current?.[key] ?? '';
    if (key === 'error_type' && value && input.options && ![...input.options].some(option => option.value === value)) {
      const option = input.ownerDocument.createElement('option');
      option.value = option.textContent = value; input.append(option);
    }
    input.value = value;
  }
  $('[data-truth-status]', root).textContent = '可编辑';
  $('[data-sample-annotations]', root).disabled = false;
  $('[data-save-sample]', root).disabled = false;
  syncSampleDate(ui, root);
  const history = truth.history || [];
  // Timestamps and action names come from the local audit, not editable text.
  $('[data-truth-history]', root).textContent = history.length
    ? `最近保存：${history[0].changed_at}` : '尚无标注修改记录';
}

export function collectSampleAnnotations(ui, root, revision) {
  const value = name => ui.$(`[name=${name}]`, root).value;
  const present = value('truth_date_present');
  const match = value('truth_seal_should_match');
  return {
    revision, fields: Object.fromEntries(Object.entries(fieldNames).map(([name, field]) => [field, value(name)])),
    date_present: present === '' ? null : present === 'true',
    actual_date: present === 'true' ? value('truth_actual_date') : '',
    seal_should_match: match === '' ? null : match === 'true',
    human_note: value('human_note'), error_type: value('error_type'),
  };
}

export function createSampleAnnotationSubmission({ui, reviewState, services, renderHistory, refreshVisibleResults}) {
  const {$, $$, toast} = ui;
  return async function saveSampleAnnotations() {
    if (!reviewState.sampleView || reviewState.reviewSaving) return;
    if (!reviewState.sampleAnnotationRevision) return toast('标注正在加载，请稍候再保存', 'warning');
    const root = $('#review-content'), id = reviewState.current.id;
    const payload = collectSampleAnnotations(ui, root, reviewState.sampleAnnotationRevision);
    if (payload.date_present === true && !payload.actual_date) return toast('请填写实际签收日期', 'warning');
    const controls = $$('button,input,textarea,select', root).map(el => [el, el.disabled]);
    reviewState.reviewSaving = true;
    reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
    controls.forEach(([el]) => el.disabled = true);
    $('[data-save-state]').textContent = '正在保存…';
    let saved;
    try {
      saved = await services.samples.saveAnnotations(id, payload);
      reviewState.reviewDirty = false;
      $('[data-save-state]').textContent = '已保存';
      $('[data-save-state]').classList.remove('unsaved');
      toast('样单标注与备注已保存');
    } catch (error) {
      $('[data-save-state]').textContent = '保存失败，修改已保留';
      toast(error.message, 'danger');
    } finally {
      reviewState.reviewSaving = false;
      controls.forEach(([el, disabled]) => el.disabled = disabled);
    }
    if (saved) {
      renderSampleAnnotations(ui, reviewState, saved, root);
      await Promise.allSettled([renderHistory(), refreshVisibleResults()]);
    }
  };
}
