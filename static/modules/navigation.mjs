import {localToday} from './ui.mjs';
export function createNavigation({
  environment, ui, navigationState, recordsState, reportState, reviewState, canLeaveReview, dismissReview,
  openReview, showReviewEmpty, showImportDialog, loadReviewQueue, loadDailyResults, loadRecords, loadReport
}) {
  const {document, window, location, history} = environment;
  const {$, $$, toast} = ui;

  const pageInfo = {
    records: ['回单记录', '查询回单，优先处理需要你确认的项目。'],
    import: ['导入回单', '上传一批回单，跟进识别进度，再完成复核与导出。'],
    quality: ['质量统计', '查看识别准确率、错误分布与样单评测结果。'],
    settings: ['设置', '选择识别内容与方式，让每次测试更有针对性。'],
    progress: ['工作台', '处理待办，跟进回单识别进度。'],
    review: ['人工复核', '对照原图核验信息，确认后保存结果。'],
  };
  function syncReviewDialog(page) {
    const dialog = $('#review-dialog');
    if (!dialog) return;
    if (page === 'review' && !dialog.open) {
      const table = $('.record-filter-table');
      navigationState.reviewReturn = {
        focus: document.activeElement,
        recordId: document.activeElement?.closest('[data-open-review]')?.dataset.openReview,
        scroll: reviewState.reviewOrigin?.hash === '#records' ? reviewState.reviewOrigin.scroll : 0,
        tableTop: table?.scrollTop || 0, tableLeft: table?.scrollLeft || 0,
      };
      reviewState.reviewOrigin = {hash: '#records', scroll: navigationState.reviewReturn.scroll};
      const toastNode = $('#toast');
      navigationState.toastParent = toastNode?.parentElement;
      if (toastNode) dialog.append(toastNode);
      document.body.classList.add('review-dialog-open');
      dialog.showModal();
      if (!recordsState.recordsLoaded) loadRecords().catch(error => toast(error.message, 'danger'));
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
      navigationState.reviewReturn = null;
    }
  }
  function showPage(page) {
    if (!pageInfo[page]) page = 'records';
    if ($('#import-dialog').open) $('#import-dialog').close();
    const backgroundPage = page === 'review' ? 'records' : page;
    $$('[data-page]').forEach(section => section.classList.toggle('hidden', section.dataset.page !== page && section.dataset.page !== backgroundPage));
    $$('[data-nav]').forEach(link => {
      const active = link.dataset.nav === backgroundPage;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    $('#page-title').textContent = pageInfo[backgroundPage][0];
    $('#page-subtitle').textContent = pageInfo[backgroundPage][1];
    document.title = `${pageInfo[page][0]} · 三星回单核验台`;
    document.body.dataset.activePage = page;
    syncReviewDialog(page);
    const reviewEntry = $('#open-batch-review');
    const day = $('#progress-date').value || localToday();
    reviewEntry.setAttribute('aria-label', page === 'records' ? '人工复核当前筛选' : `人工复核，${day}，当天全部`);
    reviewEntry.title = page === 'records' ? '复核当前筛选范围内的回单' : `复核 ${day} 当天的回单`;
    reviewEntry.dataset.reviewDay = page === 'records' ? '' : day;
    reviewEntry.classList.remove('has-pending');
    $('#review-entry-count').textContent = '';
    $('#review-entry-count').classList.add('hidden');
    if (page !== 'review') {
      window.scrollTo(0, navigationState.restoreScroll || 0); navigationState.restoreScroll = 0;
    }
  }
  function routePage() {
    const route = location.hash.slice(1) || 'progress';
    if (reviewState.current && !/^(review|view)\/\d+$/.test(route) && !canLeaveReview()) { history.replaceState(null, '', `#${reviewState.reviewMode || 'review'}/${reviewState.current.id}`); return; }
    if (route === 'import') { showPage('records'); showImportDialog(); return; }
    if (/^(review|view)\/\d+$/.test(route)) {
      const id = Number(route.split('/')[1]);
      const mode = route.startsWith('view/') ? 'view' : 'review';
      if (reviewState.current?.id === id && reviewState.reviewMode === mode) showPage('review');
      else openReview(id, {mode}).catch(error => {
        if (location.hash !== `#${mode}/${id}`) return;
        toast(error.message, 'danger');
        if (reviewState.current) history.replaceState(null, '', `#${reviewState.reviewMode || 'review'}/${reviewState.current.id}`);
        else location.hash = 'records';
      });
    } else {
      if (route === 'review' && !reviewState.current) showReviewEmpty?.({loading: true});
      showPage(route);
      if (route === 'review') loadReviewQueue(true).catch(error => {
        if (!location.hash.startsWith('#review')) return;
        if (!reviewState.current) showReviewEmpty?.({error: error.message});
        toast(error.message, 'danger');
      });
      if (route === 'progress') loadDailyResults().catch(error => toast(error.message, 'danger'));
      if (route === 'records') loadRecords({background: !!recordsState.recordsLoaded}).catch(error => toast(error.message, 'danger'));
      if (route === 'quality' && (reportState.reportStale || !reportState.reportLoadedAt || Date.now() - reportState.reportLoadedAt >= 30000)) loadReport().catch(error => toast(error.message, 'danger'));
    }
  }

  function initialize() {
    window.addEventListener('hashchange', routePage);

  }

  return {initialize, showPage, routePage};
}
