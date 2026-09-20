/** Modal confirmation; retain the export name for existing retry callers. */
export function confirmInline({environment, anchor, message, title = '确认重新识别', confirmLabel = '继续重试'}) {
  const {document, window} = environment;
  const fallback = () => Promise.resolve(typeof window?.confirm === 'function' ? window.confirm(message) : false);
  if (!document?.createElement || !document.body || typeof document.body.append !== 'function') {
    return fallback();
  }
  const panel = document.createElement('dialog');
  if (typeof panel.showModal !== 'function') return fallback();
  const previousFocus = anchor || document.activeElement;
  panel.className = 'retry-confirm-dialog';
  panel.setAttribute('role', 'alertdialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-labelledby', 'retry-confirm-title');
  panel.setAttribute('aria-describedby', 'retry-confirm-message');
  panel.innerHTML = '<h2 id="retry-confirm-title"></h2><p id="retry-confirm-message"></p><div class="retry-confirm-actions"><button type="button" data-inline-confirm-cancel autofocus>取消</button><button type="button" data-inline-confirm-ok></button></div>';
  panel.querySelector('h2').textContent = title;
  panel.querySelector('p').textContent = message;
  panel.querySelector('[data-inline-confirm-ok]').textContent = confirmLabel;
  document.body.append(panel);

  return new Promise(resolve => {
    let settled = false;
    const cleanup = value => {
      if (settled) return;
      settled = true;
      if (panel.open) panel.close();
      panel.remove();
      if (previousFocus?.isConnected) previousFocus.focus?.({preventScroll: true});
      resolve(value);
    };
    // Keep review-page save/approve shortcuts from firing beneath the modal.
    panel.addEventListener('keydown', event => event.stopPropagation());
    panel.addEventListener('cancel', event => { event.preventDefault(); cleanup(false); });
    panel.addEventListener('close', () => cleanup(false));
    panel.querySelector('[data-inline-confirm-cancel]')?.addEventListener('click', () => cleanup(false));
    panel.querySelector('[data-inline-confirm-ok]')?.addEventListener('click', () => cleanup(true));
    try { panel.showModal(); }
    catch (_) { cleanup(false); return; }
    panel.querySelector('[data-inline-confirm-cancel]')?.focus?.();
  });
}
