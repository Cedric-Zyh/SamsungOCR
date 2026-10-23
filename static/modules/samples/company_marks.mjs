import {escapeHtml as esc} from '../core/ui.mjs';

export const COMPANY_MARK_LABELS = {unmarked: '未标记', pending: '待检查', checked: '已检查', issue: '有问题'};

export function companyMatchesMark(mark, filter) {
  return !filter || (mark?.state || 'unmarked') === filter;
}

export function companySealDescription(seal) {
  return seal?.has_standard ? `印章中心已收录 ${seal.matched}/${seal.total} 种签章要求${seal.unlabeled ? `；${seal.unlabeled} 张样单未标注签章要求` : ''}`
    : seal?.total ? '印章中心尚无对应标准章' : '样单未标注签章要求，暂不能关联标准章';
}

export function companyRowMarkup(row, progress, active, seal, expanded = null) {
  const expandable = row.name && row.name !== '未分类';
  const built = Number(seal?.matched) || 0;
  const summary = expandable ? `<span class="sample-company-built-count" data-available="${built > 0}">已建章 ${built} 个</span>` : '';
  const icon = expandable
    ? `<svg class="sample-company-chevron${expanded ? ' is-expanded' : ''}" viewBox="0 0 20 20" aria-hidden="true"><path d="m7 4 6 6-6 6"/></svg>`
    : `<svg class="sample-scope-icon" viewBox="0 0 24 24" aria-hidden="true">${row.name
      ? '<path d="M3 7V5h6l2 3h10v12H3z"/>'
      : '<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h5"/>'}</svg>`;
  return `<button type="button" data-company="${esc(row.name)}" data-progress="${progress.state}" class="sample-company-select${expandable ? ' sample-company-parent' : ' sample-company-scope'}${active ? ' active' : ''}" aria-pressed="${active}" ${expandable ? `aria-expanded="${Boolean(expanded)}"` : ''} title="${esc(progress.title)}">${icon}<span class="sample-company-copy"><span class="sample-company-name">${esc(row.name || '全部样单')}</span>${summary}</span><small class="sample-company-total">${row.count}</small></button>`;
}

export function requirementRowMarkup(company, group, active, progress) {
  const badge = group.key && group.has_standard ? '<span class="sample-requirement-standard">✓ 已建章</span>' : '';
  return `<button type="button" class="sample-requirement-select${active ? ' active' : ''}" data-company="${esc(company)}" data-requirement="${esc(group.key)}" data-progress="${progress.state}" aria-pressed="${active}"><span class="sample-requirement-copy"><span class="sample-requirement-name">${esc(group.name)}</span>${badge}</span><small class="sample-requirement-total">${group.count}</small></button>`;
}

export function createCompanyMarks({environment, ui, services, render}) {
  const {$, $$, toast} = ui;
  let marks = {}, versions = {}, seals = {}, editing = null, saving = false, filter = '';
  const dialog = () => $('#sample-company-mark-dialog');
  const chosenState = () => $('#sample-company-mark-form input[name="company-mark-state"]:checked').value;

  function update(data) {
    for (const [name, mark] of Object.entries(data.company_marks || {})) {
      if ((marks[name]?.revision || 0) <= mark.revision) marks[name] = mark;
    }
    versions = data.company_versions || {};
    seals = data.company_seals || {};
  }

  function renderHeader(company) {
    const panel = $('#sample-company-mark-panel');
    panel.hidden = !company;
    if (!company) return;
    const mark = marks[company] || {state: 'unmarked', note: ''};
    const status = $('#sample-company-header-mark');
    status.dataset.mark = mark.state;
    status.textContent = `${mark.state === 'checked' ? '✓ ' : ''}${COMPANY_MARK_LABELS[mark.state] || '未标记'}`;
    $('#sample-company-header-new').hidden = !mark.has_new_results;
    const sealBadge = $('#sample-company-header-seal');
    sealBadge.hidden = true;
    const note = $('#sample-company-header-note');
    note.hidden = !mark.note;
    note.textContent = mark.note ? `备注：${mark.note}` : '';
    note.title = mark.note || '';
    const button = $('#sample-company-mark-edit');
    button.dataset.markCompany = company;
    button.textContent = mark.state === 'unmarked' && !mark.note ? '添加标记与备注' : '编辑标记与备注';
  }

  function syncReviewed() {
    $('#sample-company-reviewed-row').hidden = chosenState() !== 'checked';
  }

  function show(company) {
    if (saving) return;
    const mark = marks[company] || {state: 'unmarked', note: '', revision: 0};
    editing = {company, revision: mark.revision, state: mark.state, result_version: versions[company]};
    $('#sample-company-mark-name').textContent = company;
    $$('input[name="company-mark-state"]').forEach(input => { input.checked = input.value === mark.state; });
    $('#sample-company-mark-note').value = mark.note;
    $('#sample-company-note-count').textContent = `${mark.note.length} / 500`;
    $('#sample-company-reviewed').checked = false;
    $('#sample-company-mark-hint').textContent = mark.has_new_results
      ? '有新测试结果。查看后勾选「确认已检查当前结果」，即可清除提示。'
      : '';
    $('#sample-company-mark-error').textContent = '';
    syncReviewed();
    if (!dialog().open) dialog().showModal();
  }

  async function save(event) {
    event.preventDefault();
    if (saving || !editing) return;
    saving = true;
    const payload = {...editing, state: chosenState(),
      note: $('#sample-company-mark-note').value, reviewed: $('#sample-company-reviewed').checked};
    const controls = $$('input,select,textarea,button', dialog());
    controls.forEach(control => { control.disabled = true; });
    $('#sample-company-mark-error').textContent = '';
    try {
      const mark = await services.samples.saveCompanyMark(payload);
      marks[payload.company] = mark;
      render();
      dialog().close();
      toast('公司标记和备注已保存', 'success');
    } catch (error) {
      $('#sample-company-mark-error').textContent = error.message;
    } finally {
      saving = false;
      controls.forEach(control => { control.disabled = false; });
    }
  }

  function initialize() {
    $('#sample-company-mark-filter').addEventListener('click', event => {
      const button = event.target.closest('[data-company-mark-filter]');
      if (!button) return;
      filter = button.dataset.companyMarkFilter;
      $$('[data-company-mark-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      render();
    });
    $('#sample-company-mark-edit').addEventListener('click', event => show(event.currentTarget.dataset.markCompany));
    $('#sample-company-mark-state').addEventListener('change', () => {
      $('#sample-company-reviewed').checked = chosenState() === 'checked' && editing?.state !== 'checked';
      syncReviewed();
    });
    $('#sample-company-mark-note').addEventListener('input', event => {
      $('#sample-company-note-count').textContent = `${event.target.value.length} / 500`;
    });
    $('#sample-company-mark-form').addEventListener('submit', save);
    dialog().addEventListener('cancel', event => { if (saving) event.preventDefault(); });
    dialog().addEventListener('close', () => {
      const button = $('#sample-company-mark-panel').hidden ? $('#sample-company-mark-filter [aria-pressed="true"]') : $('#sample-company-mark-edit');
      button.focus({preventScroll: true});
    });
    environment.window.addEventListener('hashchange', () => dialog().close());
  }

  return {initialize, update, renderHeader, seal: name => seals[name],
    matches: name => companyMatchesMark(marks[name], filter)};
}
