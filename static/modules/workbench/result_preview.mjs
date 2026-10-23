import {escapeHtml, filenameParts} from '../core/ui.mjs';
import {simplifySealText} from '../core/seal_script.mjs';

export function resultPreviewModel(record, workbench) {
  const fields = record.fields || {}, date = record.date_check || {}, seal = record.seal_check || {};
  const signature = workbench.signatureCheck(record);
  const rows = [
    {key: 'date', label: '日期', expected: fields['要求到货'] || date.required, actual: date.actual},
    {key: 'seal', label: '印章', expected: fields['签章要求'] || seal.requirement, actual: simplifySealText(seal.recognized)},
    {key: 'signature', label: '签名', expected: signature.contact, actual: signature.receiver},
  ].map(row => {
    const status = workbench.checkStatus(record, row.key);
    const tone = status === '匹配' ? 'success' : status === '未执行' ? 'neutral' : 'warning';
    return {...row, status, tone, expected: row.expected || '未提供',
      actual: row.actual || (status === '未执行' ? '未执行' : '未识别')};
  });
  const attention = rows.filter(row => row.tone === 'warning');
  const category = workbench.category(record);
  return {rows, attention, status: ({passed: '已通过', rejected: '不通过', review: '待复核', failed: '识别失败'})[category] || '已完成',
    tone: category === 'passed' ? 'success' : 'warning'};
}

export function renderResultPreview(model) {
  const {rows, attention} = model;
  const summary = attention.length ? `${attention.length} 项待确认` : '核验结果已加载';
  const detail = attention.length ? attention.map(row => `${row.label} · ${row.status}`).join('；')
    : rows.some(row => row.status === '未执行') ? '部分项目未执行，请按需要进入复核。' : '日期、印章、签名均匹配。';
  return `<div class="result-preview-summary" data-tone="${attention.length ? 'warning' : 'neutral'}"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16v1"/></svg><strong>${summary}</strong><span>${escapeHtml(detail)}</span></div>
    <div class="result-preview-table-wrap"><table class="result-preview-table"><colgroup><col class="preview-label-col"><col><col><col class="preview-status-col"></colgroup>
    <thead><tr><th scope="col">核验项目</th><th scope="col">要求值</th><th scope="col">识别值</th><th scope="col">结果</th></tr></thead>
    <tbody>${rows.map(row => `<tr data-tone="${row.tone}"><th scope="row">${row.label}</th><td>${escapeHtml(row.expected)}</td><td>${escapeHtml(row.actual)}</td><td><span class="result-preview-badge" data-tone="${row.tone}">${escapeHtml(row.status)}</span></td></tr>`).join('')}</tbody></table></div>
    <p class="result-preview-note"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/></svg>${attention.length ? '核对或修改识别内容，请进入复核。' : '此处展示已保存的结果，可进入复核查看详情或修改。'}</p>`;
}

export function createResultPreview({environment, ui, services, ReceiptWorkbench, enterReview}) {
  const {$, toast} = ui;
  let request = 0, currentId = null, trigger = null, ownsScrollLock = false;
  const dialog = () => $('#result-preview-dialog');
  function cleanup() {
    request++; currentId = null;
    if (ownsScrollLock) environment.document.body.classList.remove('modal-open');
    ownsScrollLock = false;
    if (trigger?.isConnected) trigger.focus({preventScroll: true});
    trigger = null;
  }
  function close() {
    if (dialog()?.open) dialog().close();
    cleanup();
  }
  async function open(id, source) {
    if (!Number.isSafeInteger(id) || id <= 0 || !dialog()) return;
    const ticket = ++request;
    currentId = null;
    trigger = source || environment.document.activeElement;
    $('#result-preview-filename').textContent = '正在加载单据…';
    $('#result-preview-status').hidden = true;
    $('#result-preview-review').disabled = true;
    $('#result-preview-body').setAttribute('aria-busy', 'true');
    $('#result-preview-body').innerHTML = '<p class="result-preview-loading" role="status">正在加载识别结果…</p>';
    if (!dialog().open) {
      ownsScrollLock = !environment.document.body.classList.contains('modal-open');
      environment.document.body.classList.add('modal-open');
      dialog().showModal();
    }
    try {
      const record = await services.records.get(id);
      if (ticket !== request || !dialog().open) return;
      if (Number(record.id) !== id) throw Error('单据结果不一致，请关闭后重试。');
      const model = resultPreviewModel(record, ReceiptWorkbench);
      $('#result-preview-filename').textContent = filenameParts(record.filename || '').name || `回单 #${id}`;
      const status = $('#result-preview-status');
      status.textContent = model.status; status.dataset.tone = model.tone; status.hidden = false;
      $('#result-preview-body').innerHTML = renderResultPreview(model);
      currentId = id;
      $('#result-preview-review').disabled = false;
    } catch (error) {
      if (ticket !== request || !dialog().open) return;
      $('#result-preview-filename').textContent = '结果暂时无法加载';
      $('#result-preview-body').innerHTML = `<div class="result-preview-loading" role="alert"><p>${escapeHtml(error.message || '加载失败，请稍后重试。')}</p><button type="button" class="secondary-button" data-preview-retry="${id}">重新加载</button></div>`;
    } finally {
      if (ticket === request) $('#result-preview-body').setAttribute('aria-busy', 'false');
    }
  }
  function initialize() {
    const node = dialog();
    if (!node) return;
    node.addEventListener('cancel', event => { event.preventDefault(); close(); });
    node.addEventListener('close', () => {
      // A delayed close event must not invalidate a newly opened preview.
      if (node.open) return;
      cleanup();
    });
    node.addEventListener('click', async event => {
      if (event.target.closest('[data-preview-close]')) { close(); return; }
      if (event.target === node) {
        const rect = node.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
        return;
      }
      const retry = event.target.closest('[data-preview-retry]');
      if (retry) { await open(Number(retry.dataset.previewRetry), trigger); return; }
      if (event.target.closest('#result-preview-review') && currentId) {
        const id = currentId;
        close();
        try { await enterReview(id); } catch (error) { toast(error.message, 'danger'); }
      }
    });
    environment.window.addEventListener('hashchange', close);
  }
  return {initialize, open, close};
}
