/** Keep the statistics dialog and its exact list position behind a sample. */
export function createStatisticsPreview({environment, ui, openResult, openOriginal, imageUrl, onReturn}) {
  const {$} = ui;
  const dialog = () => $('#sample-statistics-dialog');
  const body = () => $('#sample-statistics-body');
  const table = () => body()?.querySelector?.('.sample-stat-table-wrap');
  const route = () => environment.location?.hash || environment.window.location?.hash || '#samples';
  let session = null;

  function clear() { session = null; }

  function finish(entry) {
    if (session !== entry) return;
    clear();
    // Navigation and native close events can arrive in separate tasks. Never
    // reopen statistics or move focus back after the user leaves this page.
    if (!dialog()?.open || route() !== '#samples') return;
    const content = body(), list = table();
    if (content) content.scrollTop = entry.bodyTop;
    if (list) { list.scrollTop = entry.tableTop; list.scrollLeft = entry.tableLeft; }
    const focus = entry.focus?.isConnected ? entry.focus : $('#sample-statistics-details-tab');
    focus?.focus?.({preventScroll: true});
    onReturn?.();
  }

  async function open(row, browseIds, button) {
    if (session || !dialog()?.open) return;
    const entry = session = {
      panel: $(row.result_id ? '#review-dialog' : '#original-preview-dialog'),
      focus: button, bodyTop: body()?.scrollTop || 0,
      tableTop: table()?.scrollTop || 0, tableLeft: table()?.scrollLeft || 0,
      opened: false,
    };
    $('#sample-statistics-feedback').textContent = '';
    const isCurrent = () => session === entry && !!dialog()?.open;
    try {
      if (row.result_id) await openResult(row.result_id, {browseIds: [...browseIds], isCurrent,
        onOpened: () => { if (isCurrent()) entry.opened = true; }});
      else await openOriginal({filename: row.filename, sample_center: true, preview_url: imageUrl(row.filename)}, button);
      if (!isCurrent()) return;
      entry.opened = !!entry.panel?.open;
      if (!entry.opened) finish(entry);
    } catch (error) {
      if (!isCurrent()) return;
      $('#sample-statistics-feedback').textContent = `样单加载失败：${error.message}。请重试。`;
      finish(entry);
    }
  }

  function initialize() {
    for (const selector of ['#review-dialog', '#original-preview-dialog']) {
      const panel = $(selector);
      panel?.addEventListener('close', () => {
        // A close queued by a prior viewing must not finish a fresh opening.
        if (!panel.open && session?.panel === panel && session.opened) finish(session);
      });
    }
    dialog()?.addEventListener('close', () => { if (!dialog().open) clear(); });
    environment.window.addEventListener('hashchange', () => {
      // Navigation may reject leaving an unsaved sample and restore its hash.
      // Its listener is registered after ours, so inspect the final route.
      Promise.resolve().then(() => {
        const current = route();
        if (current === '#samples' || (session && /^#sample-view\/\d+$/.test(current))) return;
        clear();
        if (dialog()?.open) dialog().close();
      });
    });
  }

  return {initialize, active: () => !!session, open, clear};
}
