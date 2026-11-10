import {escapeHtml} from '../core/ui.mjs';

const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const fieldValue = field => {
  const value = object(field).value;
  return (typeof value === 'number' || typeof value === 'string') && String(value).trim()
    ? String(value) : '未识别';
};
const fieldList = fields => `<dl class="dzt-result-fields">${Object.entries(fields).map(([key, field]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(fieldValue(field))}</dd>`).join('')}</dl>`;
const sealStatus = status => ({queued: '待提交', uploading: '正在上传', submitting: '正在提交',
  waiting: '等待结果', received: '已返回', completed: '已返回', applied: '已完成',
  failed: '识别失败', rejected: '提交被拒绝', unknown: '提交状态待确认',
  submission_unknown: '提交状态待确认', cancelled: '已取消'})[status] || status || '状态未记录';
const canQuery = task => typeof task?.queryable === 'boolean'
  ? task.queryable : ['waiting', 'received'].includes(task?.status);

export function remoteResultMarkup(detail) {
  const sealResult = object(detail.seal_result);
  const hasSceneState = detail.seal_result != null && detail.seal_result === sealResult;
  const hasSealResult = hasSceneState && sealResult.seal_required !== false
    && (sealResult.seal_required === true || typeof sealResult.prepared === 'boolean' || Array.isArray(sealResult.requests));
  const pageState = object(sealResult.page);
  const hasPageState = hasSceneState && sealResult.page_required !== false && Object.keys(pageState).length > 0;
  const fields = Object.fromEntries(Object.entries(object(detail.result?.commitResult))
    .filter(([key]) => !hasSealResult || key !== '收货客户印章'));
  const page = Object.keys(fields).length || hasPageState
    ? `${hasSealResult || hasPageState ? `<h4>整页识别${hasPageState ? ` · ${escapeHtml(sealStatus(pageState.status))}` : ''}</h4>` : ''}${hasPageState && pageState.error ? `<p class="error-text">${escapeHtml(pageState.error)}</p>` : ''}${Object.keys(fields).length ? fieldList(fields) : '<p>暂无整页识别结果。</p>'}` : '';
  let seals = '';
  if (hasSealResult) {
    const requests = Array.isArray(sealResult.requests) ? sealResult.requests.filter(item => item && typeof item === 'object') : [];
    const content = requests.map((request, position) => {
      const index = Number.isInteger(request.index) && request.index >= 0 ? request.index + 1 : position + 1;
      const commit = object(request.result?.commitResult);
      const values = Object.fromEntries(['收货客户印章', '印章颜色', '印章类型']
        .map(key => [key, commit[key]]));
      return `<section class="dzt-seal-result"><h4>印章 ${index} · ${escapeHtml(sealStatus(request.status))}</h4>${request.error ? `<p class="error-text">${escapeHtml(request.error)}</p>` : ''}${fieldList(values)}<p class="dzt-request-id">印章请求编号：${escapeHtml(request.req_uuid || '尚未取得')}</p></section>`;
    }).join('');
    const preparationError = sealResult.preparation_error || sealResult.error;
    seals = `<section class="dzt-seal-results"><h4>印章专用场景</h4>${preparationError ? `<p class="error-text">${escapeHtml(preparationError)}</p>` : ''}${content || `<p>${preparationError ? '印章准备失败。' : sealResult.prepared ? '未检测到可提交单证通的印章。' : '印章尚未准备完成。'}</p>`}</section>`;
  }
  const pageRequestId = Object.hasOwn(detail, 'page_req_uuid') ? detail.page_req_uuid : detail.req_uuid;
  const requestId = sealResult.page_required !== false && (!hasSealResult || pageRequestId || hasPageState)
    ? `<p class="dzt-request-id">${hasSealResult || hasPageState ? '整页请求编号' : '请求编号'}：${escapeHtml(pageRequestId || '尚未取得')}</p>` : '';
  const submitted = typeof detail.submitted_at === 'string' ? detail.submitted_at.replace('T', ' ') : '';
  return `<p>${escapeHtml(detail.label || '')}${submitted ? ` · 提交于 ${escapeHtml(submitted)}` : ''}</p>${detail.error_message ? `<p class="error-text">${escapeHtml(detail.error_message)}</p>` : ''}${page}${seals}${requestId}`;
}

export function remoteAction(job, {disabled = false, compact = false} = {}) {
  if (!job?.id || ['awaiting_upload', 'cancelled'].includes(job.status)) return '';
  const task = job.danzhengtong;
  let action = 'submit', label = '提交单证通';
  if (task) {
    if (task.status === 'waiting' || task.status === 'queued' && task.queryable === true) { action = 'query'; label = task.error_message ? '再次查询' : '查询结果'; }
    else if (['queued', 'submitting'].includes(task.status)) { action = ''; label = task.status === 'queued' ? '等待提交…' : '正在提交…'; }
    else { action = 'view'; label = ['received', 'applied', 'conflict'].includes(task.status) ? '查看结果' : '查看原因'; }
    if (task.querying) { action = ''; label = '正在查询…'; }
  }
  if (compact && (!task || !(task.status === 'waiting' || task.status === 'queued' && task.queryable === true))) return '';
  return `${compact ? '' : `<span class="dzt-row-status">${task ? escapeHtml(task.label) : '单证通未提交'}</span>`}<button type="button" class="mini-button${compact ? ' row-dzt-query' : ''}" data-dzt-action="${action}" data-dzt-job="${escapeHtml(job.id)}" title="${compact ? '查询单证通结果' : label}" ${disabled || !action ? 'disabled' : ''}>${label}</button>`;
}

export function createDanzhengtongControls({environment, ui, services, progressState, recordsState, refresh}) {
  const {$, toast} = ui, {document} = environment;
  let control = null, busy = false;
  let viewRequest = 0;
  if (!services.danzhengtong) return {initialize() {}, render() {}};
  const jobs = () => [...(progressState.queueJobs?.values() || [])];
  function render() {
    if (progressState.dztControl) control = progressState.dztControl;
    const tasks = jobs().map(job => job.danzhengtong).filter(Boolean);
    const counts = {done: 0, waiting: 0, submitting: 0, failed: 0};
    for (const task of tasks) {
      if (task.status === 'applied') counts.done++;
      else if (['waiting', 'received'].includes(task.status)) counts.waiting++;
      else if (['queued', 'submitting'].includes(task.status)) counts.submitting++;
      else counts.failed++;
    }
    const returned = tasks.filter(task => ['received', 'applied', 'conflict'].includes(task.status)).length;
    const summary = [tasks.length && `已返回 ${returned} / ${tasks.length}`, counts.waiting && `待返回 ${counts.waiting}`, counts.submitting && `待提交 ${counts.submitting}`,
      counts.failed && `待处理 ${counts.failed}`].filter(Boolean);
    if ($('#dzt-summary')) $('#dzt-summary').textContent = summary.join(' · ') || '暂无任务';
    const queryable = tasks.some(canQuery);
    if ($('#dzt-query-all')) { $('#dzt-query-all').disabled = busy || !queryable; $('#dzt-query-all').hidden = !queryable; }
    for (const [id, enabled] of [['#dzt-submit-toggle', !control?.submit_paused], ['#dzt-query-toggle', !!control?.auto_query]]) {
      const button = $(id);
      if (button) { button.disabled = busy || !control; button.setAttribute?.('aria-checked', String(enabled)); }
    }
    const note = $('#dzt-auto-note');
    if (note) {
      note.textContent = control ? `${control.auto_query ? '自动查询' : '手动查询'}${control.submit_paused ? ' · 新提交已暂停' : ''}` : '';
      note.dataset.enabled = String(!!control?.auto_query);
    }
  }
  async function action(ids, kind, button) {
    if (busy) return;
    busy = true; if (button) button.disabled = true; render();
    try {
      for (let offset = 0; offset < ids.length; offset += 5000) await services.danzhengtong.action(ids.slice(offset, offset + 5000), kind);
      toast(kind === 'query' ? '已安排查询原请求，不会重新上传。' : '已加入单证通提交队列。', 'success');
      await refresh();
    } catch (error) { toast(error.message, 'danger'); }
    finally { busy = false; if (button?.isConnected) button.disabled = false; render(); }
  }
  async function view(id) {
    const serial = ++viewRequest;
    if ($('#dzt-resubmit')) $('#dzt-resubmit').disabled = true;
    try {
      const detail = await services.danzhengtong.detail(id);
      if (serial !== viewRequest) return;
      const dialog = $('#dzt-result-dialog'), body = $('#dzt-result-body');
      if (!dialog || !body) return;
      body.innerHTML = remoteResultMarkup(detail);
      dialog.dataset.jobId = id;
      if ($('#dzt-resubmit')) $('#dzt-resubmit').disabled = false;
      if (!dialog.open) dialog.showModal();
    } catch (error) {
      if (serial === viewRequest) throw error;
    }
  }
  function invalidateView() {
    ++viewRequest;
    const dialog = $('#dzt-result-dialog');
    if (dialog) delete dialog.dataset.jobId;
    if ($('#dzt-resubmit')) $('#dzt-resubmit').disabled = true;
  }
  function initialize() {
    render();
    $('#dzt-query-all')?.addEventListener('click', event => {
      const selected = [...(document.querySelectorAll?.('[data-work-select]:checked') || [])].map(input => input.dataset.workSelect?.replace(/^job:/, ''));
      const records = recordsState?.selected || new Set();
      const candidates = jobs().filter(job => canQuery(job.danzhengtong));
      const ids = candidates.filter(job => selected.length ? selected.includes(job.id) : !records.size || records.has(job.result_id || job.target_result_id)).map(job => job.id);
      if (ids.length) action(ids, 'query', event.currentTarget);
      else toast('当前范围内没有可查询的单证通任务');
    });
    for (const [selector, key] of [['#dzt-submit-toggle', 'submit_paused'], ['#dzt-query-toggle', 'auto_query']]) {
      $(selector)?.addEventListener('click', async () => {
        if (!control || busy) return;
        busy = true; render();
        try { control = progressState.dztControl = await services.danzhengtong.control({[key]: !control[key]}); }
        catch (error) { toast(error.message, 'danger'); }
        finally { busy = false; render(); }
      });
    }
    document.addEventListener('click', async event => {
      for (const menu of document.querySelectorAll?.('.dzt-more[open], .recognition-start-menu[open]') || []) {
        if (!menu.contains(event.target)) menu.removeAttribute('open');
      }
      const local = event.target.closest('[data-dzt-local]');
      if (local && !local.disabled && !busy) {
        busy = true; local.disabled = true;
        try { await services.queue.start([local.dataset.dztLocal], 'local'); await refresh(); }
        catch (error) { toast(error.message, 'danger'); }
        finally { busy = false; if (local.isConnected) local.disabled = false; }
        return;
      }
      const button = event.target.closest('[data-dzt-action]');
      if (!button || button.disabled || busy) return;
      const kind = button.dataset.dztAction, id = button.dataset.dztJob;
      if (!kind || !id) return;
      event.preventDefault();
      try { if (kind === 'view') await view(id); else await action([id], kind, button); }
      catch (error) { toast(error.message, 'danger'); }
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      for (const menu of document.querySelectorAll?.('.dzt-more[open], .recognition-start-menu[open]') || []) {
        menu.removeAttribute('open'); menu.querySelector('summary')?.focus();
      }
    });
    $('#dzt-result-close')?.addEventListener('click', () => { invalidateView(); $('#dzt-result-dialog').close(); });
    $('#dzt-result-dialog')?.addEventListener('cancel', invalidateView);
    $('#dzt-result-dialog')?.addEventListener('close', () => { if (!$('#dzt-result-dialog').open) invalidateView(); });
    environment.window?.addEventListener('hashchange', () => { invalidateView(); $('#dzt-result-dialog')?.close(); });
    $('#dzt-resubmit')?.addEventListener('click', async event => {
      const dialog = $('#dzt-result-dialog'), id = dialog?.dataset.jobId;
      if (id) { await action([id], 'resubmit', event.currentTarget); dialog.close(); }
    });
  }
  return {initialize, render};
}
