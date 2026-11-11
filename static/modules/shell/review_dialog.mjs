/** The shell owns modal routes and return context. The review feature owns
 * edits, request generations and evidence cleanup through dismissReview. */
export function createReviewDialog({environment, ui, navigationState, reviewState,
  recordsState, loadRecords, dismissReview, showPage, routePage}) {
  const {document, window, location, history} = environment;
  const {$, toast} = ui;

  function backgroundPage() {
    return navigationState.reviewOrigin?.hash === '#seal-center' ? 'seal-center'
      : reviewState.sampleView ? 'samples' : 'records';
  }

  function capture({sample = false, origin = ''} = {}) {
    if (document.body.dataset.activePage === 'review') return null;
    return {hash: origin === '#seal-center' ? origin : sample ? '#samples' : '#records',
      scroll: window.scrollY || 0};
  }

  function accept(origin) {
    if (origin) navigationState.reviewOrigin = origin;
  }

  function currentHash() {
    const kind = reviewState.sampleView ? 'sample-view' : reviewState.reviewMode === 'view' ? 'view' : 'review';
    return reviewState.current ? `#${kind}/${reviewState.current.id}` : '#review';
  }

  function restoreCurrent() { history.replaceState(null, '', currentHash()); }

  function enter({id, mode = 'review', sample = false} = {}, origin) {
    accept(origin);
    const hash = id == null ? '#review' : `#${sample ? 'sample-view' : mode}/${id}`;
    const replace = document.body.dataset.activePage === 'review' || /^#(review|view|sample-view)(\/|$)/.test(location.hash);
    (replace ? history.replaceState : history.pushState || history.replaceState).call(history, null, '', hash);
    showPage('review');
  }

  function empty() { history.replaceState(null, '', '#review'); }

  function presentation() {
    const sample = !!reviewState.sampleView, page = backgroundPage();
    const statistics = sample && page !== 'seal-center' && $('#sample-statistics-dialog')?.open;
    return {
      text: statistics ? '返回统计' : '关闭',
      label: statistics ? '关闭查看样单，返回统计明细' : page === 'seal-center'
        ? `关闭查看${sample ? '样单' : '回单'}，返回印章中心`
        : sample ? '关闭查看样单，返回样单中心' : '关闭人工复核，返回回单管理',
    };
  }

  function sync(page) {
    const dialog = $('#review-dialog');
    if (!dialog) return;
    if (page === 'review' && !dialog.open) {
      const table = $('.record-filter-table'), background = backgroundPage();
      navigationState.reviewReturn = {
        focus: document.activeElement, page: background,
        recordId: document.activeElement?.closest('[data-open-review]')?.dataset.openReview,
        scroll: navigationState.reviewOrigin?.scroll || 0,
        tableTop: table?.scrollTop || 0, tableLeft: table?.scrollLeft || 0,
      };
      navigationState.reviewOrigin ||= {hash: `#${background}`, scroll: navigationState.reviewReturn.scroll};
      const toastNode = $('#toast');
      navigationState.toastParent = toastNode?.parentElement;
      if (toastNode) dialog.append(toastNode);
      document.body.classList.add('review-dialog-open');
      dialog.showModal();
      if (background === 'records' && !recordsState.recordsLoaded) loadRecords().catch(error => toast(error.message, 'danger'));
    } else if (page !== 'review' && dialog.open) {
      dismissReview?.();
      dialog.close();
      document.body.classList.remove('review-dialog-open');
      if (navigationState.toastParent) navigationState.toastParent.append($('#toast'));
      const saved = navigationState.reviewReturn;
      if (saved && page === 'records') {
        const table = $('.record-filter-table');
        if (table) { table.scrollTop = saved.tableTop; table.scrollLeft = saved.tableLeft; }
        navigationState.restoreScroll = saved.scroll;
        const focus = saved.recordId ? $(`[data-open-review="${saved.recordId}"]`) : saved.focus;
        if (focus?.isConnected && focus.closest('[data-page="records"]')) focus.focus({preventScroll: true});
        else $('#record-search')?.focus({preventScroll: true});
      }
      if (saved && (page === 'samples' || page === 'seal-center')) {
        navigationState.restoreScroll = saved.scroll;
        if (saved.focus?.isConnected) saved.focus.focus({preventScroll: true});
      }
      navigationState.reviewReturn = null;
      navigationState.reviewOrigin = null;
    }
  }

  function close() {
    const target = `#${backgroundPage()}`;
    navigationState.restoreScroll = navigationState.reviewOrigin?.scroll || 0;
    // An in-flight detail request can be dismissed before the modal opens.
    // Open dialogs are cleaned exactly once by sync() during routing.
    if (!$('#review-dialog')?.open) {
      dismissReview?.();
      navigationState.reviewOrigin = null;
      navigationState.reviewReturn = null;
    }
    history.replaceState(null, '', target);
    routePage();
  }

  function afterRefresh(page) {
    const saved = navigationState.reviewReturn;
    return async refresh => {
      const focusBefore = document.activeElement;
      await refresh();
      if (page !== 'seal-center' || saved?.page !== page || location.hash !== '#seal-center') return;
      if (document.activeElement !== focusBefore && document.activeElement !== document.body && document.activeElement?.isConnected) return;
      const focus = saved.focus?.isConnected ? saved.focus : $('[data-standard-source]', $('[data-page="seal-center"]'));
      focus?.focus({preventScroll: true});
    };
  }

  return {capture, accept, enter, empty, restoreCurrent, presentation, backgroundPage, sync, close, afterRefresh};
}
