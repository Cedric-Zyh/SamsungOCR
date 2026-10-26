import {escapeHtml} from '../core/ui.mjs';

export const problemStatusLabels = {unmarked: '尚未标注', clear: '已核对无问题', issues: '已发现问题'};
const bindings = new WeakMap();
const strings = values => Array.isArray(values) ? [...new Set(values.filter(value => typeof value === 'string' && value.trim()).map(value => value.trim()))] : [];
const checkedValues = node => strings([...node.querySelectorAll('input:checked')].map(input => input.value));

function choices(options, selected, name, suggestions = new Map(), prefix = '') {
  const checked = new Set(selected);
  return strings([...options, ...selected]).map(value => `<label class="annotation-choice"${suggestions.has(value) ? ` title="${escapeHtml(suggestions.get(value))}"` : ''}><input type="checkbox" name="${name}" value="${escapeHtml(value)}" aria-label="${escapeHtml(value)}"${checked.has(value) ? ' checked' : ''}><span>${escapeHtml(prefix && value.startsWith(prefix) ? value.slice(prefix.length) : value)}</span></label>`).join('');
}

function problemChoices(options, selected, legacy, suggestions) {
  const groups = (Array.isArray(options.groups) ? options.groups : [])
    .filter(group => group && typeof group.label === 'string' && group.label.trim() && Array.isArray(group.types));
  if (!groups.length) return `<div class="annotation-choice-list">${choices(
    [...strings(options.types), ...legacy, ...suggestions.keys()], selected, 'problem_types', suggestions)}</div>`;
  const assigned = new Set();
  const renderGroup = (label, values, prefix = '') => `<section class="annotation-problem-source" role="group" aria-label="${escapeHtml(label)}"><h4>${escapeHtml(label)}</h4><div class="annotation-choice-list">${choices(values, selected.filter(value => values.includes(value)), 'problem_types', suggestions, prefix)}</div></section>`;
  const markup = groups.map(group => {
    const values = strings(group.types).filter(value => !assigned.has(value));
    values.forEach(value => assigned.add(value));
    return values.length ? renderGroup(group.label.trim(), values,
      ['清瞳印章', '本地印章'].includes(group.label.trim()) ? group.label.trim() : '') : '';
  });
  const extra = strings([...selected, ...legacy, ...suggestions.keys()]).filter(value => !assigned.has(value));
  if (extra.length) markup.push(renderGroup('未分来源的标记', extra));
  return markup.join('');
}

function syncProblemFeedback(state) {
  const selected = new Set(checkedValues(state.types));
  for (const value of state.prefilled) if (!selected.has(value)) state.prefilled.delete(value);
  state.prefillNote.hidden = !state.prefilled.size;
  state.prefillNote.textContent = state.prefilled.size
    ? `根据已保存标注与本次结果预填 ${state.prefilled.size} 项，请核对后保存。` : '';
  state.suggestButton.hidden = ![...state.suggestions.keys()].some(value => !selected.has(value));
  state.suggestionGroup.hidden = state.prefillNote.hidden && state.suggestButton.hidden;
  state.legacyNote.hidden = !state.legacyNote.textContent || state.status.value !== 'unmarked';
  const reasons = [...state.suggestions].filter(([type]) => selected.has(type));
  if (state.reasonDetails) {
    state.reasonDetails.hidden = !reasons.length;
    state.reasonDetails.innerHTML = reasons.length
      ? `<strong>所选问题的检测依据</strong><ul>${reasons.map(([type, reason]) => `<li><b>${escapeHtml(type)}</b><span>${escapeHtml(reason)}</span></li>`).join('')}</ul>` : '';
  }
}

function bindProblemControls(types, state) {
  if (!bindings.has(types)) {
    types.addEventListener('change', event => {
      const current = bindings.get(types);
      if (event.target.name === 'problem_types') {
        current.prefilled.delete(event.target.value);
        if (event.target.checked) current.status.value = 'issues';
      }
      syncProblemFeedback(current);
    });
    state.status.addEventListener('change', () => {
      const current = bindings.get(types);
      if (current.status.value !== 'issues') {
        current.types.querySelectorAll('input').forEach(input => { input.checked = false; });
        current.prefilled.clear();
      }
      syncProblemFeedback(current);
    });
    state.suggestButton.addEventListener('click', () => {
      const current = bindings.get(types);
      current.types.querySelectorAll('input').forEach(input => {
        if (current.suggestions.has(input.value) && !input.checked) {
          input.checked = true; current.prefilled.add(input.value);
        }
      });
      if (checkedValues(types).length) current.status.value = 'issues';
      syncProblemFeedback(current);
      // Programmatic checkbox changes must also protect this draft on navigation.
      types.dispatchEvent(new Event('change', {bubbles: true}));
    });
  }
  bindings.set(types, state);
}

/** Problem observations belong to this test; image features belong to the sample. */
export function renderProblemAnnotations(ui, truth, root) {
  const $ = ui.$, status = $('[name=problem_status]', root);
  const types = $('[data-problem-types]', root), features = $('[data-sample-features]', root);
  const review = truth.problem_review || {}, options = truth.problem_options || {};
  let selected = strings(review.types);
  const legacy = strings(truth.legacy_problem_types);
  const suggestions = new Map((Array.isArray(truth.problem_suggestions) ? truth.problem_suggestions : [])
    .filter(item => item && typeof item.type === 'string' && item.type.trim())
    .map(item => [item.type.trim(), typeof item.reason === 'string' ? item.reason : '根据已保存标注与本次识别结果建议']));
  const prefilled = new Set();
  status.value = Object.hasOwn(problemStatusLabels, review.status) ? review.status : 'unmarked';
  if (truth.problem_review_saved === false && suggestions.size) {
    for (const value of suggestions.keys()) prefilled.add(value);
    selected = strings([...selected, ...prefilled]); status.value = 'issues';
  }
  types.innerHTML = problemChoices(options, status.value === 'issues' ? selected : [], legacy, suggestions);
  features.innerHTML = choices(strings(options.features), strings(truth.sample_features), 'sample_features');
  const legacyNote = $('[data-problem-legacy]', root);
  legacyNote.textContent = legacy.length
    ? `旧标记尚未关联测试：${legacy.join('、')}。核对后可在下方勾选，保存为本次测试的问题。` : '';
  const state = {types, status, suggestions, prefilled, legacyNote,
    prefillNote: $('[data-problem-prefill-note]', root), suggestButton: $('[data-problem-suggest]', root),
    suggestionGroup: $('[data-problem-suggestions]', root), reasonDetails: $('[data-problem-reasons]', root)};
  bindProblemControls(types, state);
  syncProblemFeedback(state);
  const context = truth.problem_context || {}, contextNode = $('[data-problem-context]', root);
  const methods = strings(context.methods);
  const details = [context.task_name || (context.task_id ? '样单测试' : ''),
    String(context.tested_at || '').replace('T', ' ').replace(/(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/, '')].filter(Boolean);
  contextNode.textContent = details.length ? `本次测试：${details.join(' · ')}` : '本次问题只关联当前测试结果。';
  contextNode.title = methods.length ? `识别方式：${methods.join('、')}` : '';
  const sealStatus = truth.problem_result_summary?.seal_status;
  const resultNote = $('[data-problem-result-summary]', root);
  if (resultNote) {
    resultNote.hidden = typeof sealStatus !== 'string' || !sealStatus.trim();
    resultNote.textContent = resultNote.hidden ? '' : `印章综合识别结论：${sealStatus}。下方按识别来源记录问题。`;
  }
}

export function collectProblemAnnotations(ui, root) {
  return {
    problem_status: ui.$('[name=problem_status]', root).value || 'unmarked',
    problem_types: checkedValues(ui.$('[data-problem-types]', root)),
    sample_features: checkedValues(ui.$('[data-sample-features]', root)),
  };
}

export function problemAnnotationError(payload) {
  if (payload.problem_status === 'issues' && !payload.problem_types.length) return '请选择至少一个发现的问题，或改为“尚未标注”';
  return '';
}

export function problemHistoryChanges(before, after) {
  const changed = [], oldFeatures = strings(before.sample_features), newFeatures = strings(after.sample_features);
  if (JSON.stringify([...oldFeatures].sort()) !== JSON.stringify([...newFeatures].sort())) {
    changed.push(`图片与版式特征: ${oldFeatures.join('、') || '无'} → ${newFeatures.join('、') || '无'}`);
  }
  const oldReviews = before.problem_reviews || {}, newReviews = after.problem_reviews || {};
  for (const key of new Set([...Object.keys(oldReviews), ...Object.keys(newReviews)])) {
    const oldReview = oldReviews[key] || {}, newReview = newReviews[key] || {};
    const summary = review => [problemStatusLabels[review.status] || problemStatusLabels.unmarked,
      ...strings(review.types)].join(' · ');
    if (summary(oldReview) !== summary(newReview)) {
      const taskName = newReview.task_name || oldReview.task_name || newReview.context?.task_name || oldReview.context?.task_name;
      changed.push(`问题标记（${taskName || `测试结果 ${key}`}）: ${summary(oldReview)} → ${summary(newReview)}`);
    }
  }
  return changed;
}
