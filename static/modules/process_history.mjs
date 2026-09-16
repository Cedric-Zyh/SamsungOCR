import {escapeHtml, filenameParts} from './ui.mjs';

const STATUS_TONE = {
  awaiting_upload: 'waiting',
  ready: 'waiting',
  queued: 'waiting',
  running: 'running',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'danger',
  review: 'review',
  '确认通过': 'success',
  '确认不通过': 'danger',
  '待复核': 'review',
  '无需复核': 'success'
};

export function createProcessHistory({environment, ui, api}) {
  const {document, URLSearchParams} = environment;
  const {$, toast} = ui;

  function formatTime(value) {
    if (!value) return '时间未记录';
    return String(value).replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '');
  }

  function tone(event) {
    return STATUS_TONE[event.status] || STATUS_TONE[event.kind] || '';
  }

  function render(data) {
    const file = filenameParts(data.filename || '');
    $('#process-history-title').textContent = file.name || '流程记录';
    $('#process-history-subtitle').textContent = [data.current_status ? `当前：${data.current_status}` : '', data.result_id ? `记录 #${data.result_id}` : '']
      .filter(Boolean).join(' · ');
    const events = data.events || [];
    $('#process-history-content').innerHTML = events.length ? `
      <ol class="process-timeline">
        ${events.map(event => `<li class="process-event" data-tone="${escapeHtml(tone(event))}">
          <span class="process-dot" aria-hidden="true"></span>
          <div class="process-event-main">
            <div class="process-event-head"><strong>${escapeHtml(event.title || '流程节点')}</strong><time>${escapeHtml(formatTime(event.time))}</time></div>
            ${event.detail ? `<p>${escapeHtml(event.detail)}</p>` : ''}
          </div>
        </li>`).join('')}
      </ol>` : '<div class="empty-state">暂无流程记录</div>';
  }

  async function open({jobId = '', resultId = '', filename = ''} = {}) {
    const dialog = $('#process-history-dialog');
    if (!dialog) return;
    const params = new URLSearchParams();
    if (jobId) params.set('job_id', jobId);
    if (resultId) params.set('result_id', resultId);
    $('#process-history-title').textContent = filename ? filenameParts(filename).name : '流程记录';
    $('#process-history-subtitle').textContent = '正在加载…';
    $('#process-history-content').innerHTML = '<div class="empty-state">正在加载流程记录…</div>';
    if (!dialog.open) {
      document.body.classList.add('modal-open');
      dialog.showModal();
    }
    try {
      render(await api(`/api/process-history?${params}`));
    } catch (error) {
      $('#process-history-subtitle').textContent = '加载失败';
      $('#process-history-content').innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
      toast(error.message, 'danger');
    }
  }

  function close() {
    const dialog = $('#process-history-dialog');
    if (dialog?.open) dialog.close();
  }

  function initialize() {
    const dialog = $('#process-history-dialog');
    if (!dialog) return;
    dialog.addEventListener('close', () => document.body.classList.remove('modal-open'));
    dialog.addEventListener('click', event => {
      if (event.target.closest('[data-process-close]')) close();
      if (event.target === dialog) close();
    });
  }

  return {initialize, open, close};
}
