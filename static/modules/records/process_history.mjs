import {escapeHtml, filenameParts} from '../core/ui.mjs';
import {createApiClients} from '../core/api.mjs';
import {buildProcessHistory} from './process_history_model.mjs';
import {renderProcessHistory, executionLabel, formatTime} from './process_history_view.mjs';

export function createProcessHistory({environment, ui, api, services = createApiClients(api)}) {
  services = services && typeof services === 'object' ? services : createApiClients(api);
  const {document, URLSearchParams} = environment;
  const {$, toast} = ui;
  let requestSerial = 0;

  function render(data) {
    const file = filenameParts(data.filename || '');
    $('#process-history-title').textContent = file.name || '流程记录';
    const model = buildProcessHistory(data);
    $('#process-history-subtitle').textContent = [model.events[0]?.time ? formatTime(model.events[0].time) : '', data.result_id ? `记录 #${data.result_id}` : '']
      .filter(Boolean).join(' · ');
    $('#process-history-status').textContent = executionLabel(model.status);
    $('#process-history-status').dataset.state = model.status;
    $('#process-history-content').innerHTML = renderProcessHistory(model);
    $('#process-history-logs-toggle').disabled = !model.events.length;
  }

  async function open({jobId = '', resultId = '', filename = ''} = {}) {
    const dialog = $('#process-history-dialog');
    if (!dialog) return;
    const serial = ++requestSerial;
    const params = new URLSearchParams();
    if (jobId) params.set('job_id', jobId);
    if (resultId) params.set('result_id', resultId);
    $('#process-history-title').textContent = filename ? filenameParts(filename).name : '流程记录';
    $('#process-history-subtitle').textContent = '正在加载…';
    $('#process-history-status').textContent = '';
    $('#process-history-logs-toggle').disabled = true;
    $('#process-history-logs-toggle').setAttribute('aria-expanded', 'false');
    $('[data-process-log-label]').textContent = '查看完整日志';
    $('#process-history-content').innerHTML = '<div class="empty-state">正在加载流程记录…</div>';
    $('#process-history-content').scrollTop = 0;
    if (!dialog.open) {
      document.body.classList.add('modal-open');
      dialog.showModal();
    }
    try {
      const data = await services.records.processHistory(params);
      if (serial === requestSerial && dialog.open) render(data);
    } catch (error) {
      if (serial !== requestSerial || !dialog.open) return;
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
    dialog.addEventListener('close', () => { ++requestSerial; document.body.classList.remove('modal-open'); });
    $('#process-history-logs-toggle').addEventListener('click', () => {
      const log = $('#process-history-log');
      if (!log) return;
      const expanded = log.classList.contains('hidden');
      log.classList.toggle('hidden', !expanded);
      $('#process-history-logs-toggle').setAttribute('aria-expanded', String(expanded));
      $('[data-process-log-label]').textContent = expanded ? '收起完整日志' : '查看完整日志';
      if (expanded) { log.focus({preventScroll:true}); log.scrollIntoView({block:'start'}); }
    });
    dialog.addEventListener('click', event => {
      if (event.target.closest('[data-process-close]')) close();
      if (event.target === dialog) close();
    });
  }

  return {initialize, open, close};
}
