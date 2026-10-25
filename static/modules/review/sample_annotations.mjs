/** Sample labels never overwrite the machine evidence used by comparisons. */
import {normalizedReviewDate} from './review_readiness.mjs';
const fieldNames = {truth_customer: '客户名称', truth_requirement: '签章要求', truth_required_date: '要求到货'};
const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const prefillBindings = new WeakMap();

function recognitionText(value) {
  const text = typeof value === 'string' ? value.trim() : '';
  return /^(?:未提供|未识别|未执行|未检出|未检测到|待识别|识别失败|暂无|未知|N\/A|[-—–]+)$/i.test(text) ? '' : text;
}

function updatePrefillNote(state) {
  if (!state.note) return;
  const count = state.inputs.size;
  state.note.hidden = !count;
  state.note.textContent = count ? `已根据本次识别结果带入 ${count} 项，请核对后保存。` : '';
}

function setPrefillSource(input, state, fromRecognition) {
  if (input.dataset) {
    if (fromRecognition) input.dataset.annotationSource = 'recognition';
    else delete input.dataset.annotationSource;
  }
  if (fromRecognition) state.inputs.add(input);
  if (!prefillBindings.has(input) && input.addEventListener) {
    const edited = () => {
      const current = prefillBindings.get(input);
      if (!current?.inputs.delete(input)) return;
      if (input.dataset) delete input.dataset.annotationSource;
      updatePrefillNote(current);
    };
    input.addEventListener('input', edited);
    input.addEventListener('change', edited);
  }
  prefillBindings.set(input, state);
}

function reliableRecognition(check = {}) {
  return check.reliable === true
    && !['未执行', '未识别', '未检出', '未检测到', '识别失败', '执行失败', '失败', '错误', '已跳过'].includes(check.status)
    && !['skipped', 'failed', 'error', 'pending', 'running', 'queued', 'cancelled'].includes(check.execution_status);
}

function recognizedDate(check = {}) {
  if (!reliableRecognition(check) || typeof check.actual !== 'string') return '';
  const value = check.actual.trim();
  // Only normalize a complete date; a fragment in an error or longer number is not evidence.
  if (!/^20\d{2}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}\s*日?$/.test(value)) return '';
  return normalizedReviewDate(value);
}

function recognizedSeal(check = {}) {
  if (typeof check.human_confirmed_match === 'boolean') return String(check.human_confirmed_match);
  if (!reliableRecognition(check)) return '';
  return check.status === '匹配' ? 'true' : check.status === '不匹配' ? 'false' : '';
}

export function syncSampleDate(ui, root) {
  const actual = ui.$('[name=truth_actual_date]', root);
  actual.disabled = ui.$('[name=truth_date_present]', root).value !== 'true';
}

export function renderSampleAnnotations(ui, reviewState, truth, root) {
  const {$} = ui, entry = truth.entry || {}, current = reviewState.current || {};
  const history = truth.history || [], savedBefore = history.length > 0;
  const prefill = {inputs: new Set(), note: $('[data-truth-prefill-note]', root)};
  const setValue = (name, value, fromRecognition = false) => {
    const input = $(`[name=${name}]`, root);
    input.value = value;
    setPrefillSource(input, prefill, fromRecognition);
  };
  reviewState.sampleAnnotationRevision = truth.revision;
  for (const [name, field] of Object.entries(fieldNames)) {
    const labeled = hasOwn(entry.fields || {}, field);
    const recognized = recognitionText(current.fields?.[field]);
    setValue(name, labeled ? entry.fields[field] ?? '' : recognized, !labeled && !!recognized);
  }
  const hasPresent = hasOwn(entry, 'date_present'), hasActual = hasOwn(entry, 'actual_date');
  let actualDate = normalizedReviewDate(entry.actual_date);
  let datePresent = typeof entry.date_present === 'boolean' ? String(entry.date_present)
    : !hasPresent && !savedBefore && actualDate ? 'true' : '';
  // A prior save may have deliberately cleared both date keys; do not restore that decision.
  const detectedDate = !savedBefore && !hasActual && (!hasPresent || entry.date_present === true)
    ? recognizedDate(current.date_check || {}) : '';
  if (detectedDate) {
    actualDate = detectedDate;
    if (!hasPresent) datePresent = 'true';
  }
  setValue('truth_date_present', datePresent, !!detectedDate && !hasPresent);
  setValue('truth_actual_date', actualDate, !!detectedDate);
  const detectedSeal = !savedBefore && !hasOwn(entry, 'seal_should_match')
    ? recognizedSeal(current.seal_check || {}) : '';
  setValue('truth_seal_should_match', typeof entry.seal_should_match === 'boolean'
    ? String(entry.seal_should_match) : detectedSeal, !!detectedSeal);
  updatePrefillNote(prefill);
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
