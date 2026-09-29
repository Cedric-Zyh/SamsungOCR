export function localToday() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export function filenameParts(filename) {
  const parts = String(filename || '').replaceAll('\\', '/').split('/');
  return {name: parts.pop() || '未命名图片', folder: parts.join('/')};
}

export function statusPill(status) { return `<span class="pill ${statusClass(status)}">${escapeHtml(status || '—')}</span>`; }
export function checkStatusLabel(status) {
  const label = ({'匹配待确认': '待确认', '需人工复核': '待复核', '不匹配': '不一致'})[status] || status || '—';
  return `<span class="check-status ${statusClass(status)}" title="${escapeHtml(status || '')}">${escapeHtml(label)}</span>`;
}
export function statusClass(status) { if (['通过', '匹配', '确认通过', '无需复核'].includes(status)) return 'success'; if (['不通过', '不匹配', '确认不通过', '识别失败'].includes(status)) return 'danger'; return 'warning'; }
export function percent(value) { return `${Math.round(Number(value || 0) * 100)}%`; }
export function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character])); }

export const planLabels = {fields:'印刷字段', products:'商品明细', handwriting:'手写签名', date:'签收日期', seal:'客户印章'};

export function createUi(environment) {
  const {document, setTimeout, clearTimeout} = environment;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let toastTimer;
  function toast(message, type = '') { const node = $('#toast'); node.textContent = message; node.className = `toast ${type}`; clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.add('hidden'), 3500); }

  return {$, $$, toast};
}
