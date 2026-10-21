import {localToday} from '../core/ui.mjs';
export function createNavigation({
  environment, ui, navigationState, recordsState, reviewState, canLeaveReview, dismissReview,
  openReview, showReviewEmpty, showImportDialog, loadReviewQueue, loadDailyResults, loadRecords, loadSealCenter, loadSamples
}) {
  const {document, window, location, history} = environment;
  const {$, $$, toast} = ui;

  const pageInfo = {
    samples: ['样单中心', '按客户公司管理样单，预览原图并运行测试。'],
    'seal-center': ['印章中心', '维护标准印章，让每一次核验都有据可依。'],
    records: ['回单管理', '查询与核验回单，让每一张单据都有据可查。'],
    import: ['导入回单', '上传一批回单，跟进识别进度，再完成复核与导出。'],
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
        scroll: reviewState.reviewOrigin?.scroll || 0,
        tableTop: table?.scrollTop || 0, tableLeft: table?.scrollLeft || 0,
      };
      reviewState.reviewOrigin = {hash: reviewState.sampleView ? '#samples' : '#records', scroll: navigationState.reviewReturn.scroll};
      const toastNode = $('#toast');
      navigationState.toastParent = toastNode?.parentElement;
      if (toastNode) dialog.append(toastNode);
      document.body.classList.add('review-dialog-open');
      dialog.showModal();
      if (!reviewState.sampleView && !recordsState.recordsLoaded) loadRecords().catch(error => toast(error.message, 'danger'));
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
      if (saved && page === 'samples') {
        navigationState.restoreScroll = saved.scroll;
        if (saved.focus?.isConnected) saved.focus.focus({preventScroll: true});
      }
      navigationState.reviewReturn = null;
    }
  }
  function showPage(page) {
    if (!pageInfo[page]) page = 'records';
    if ($('#import-dialog').open) $('#import-dialog').close();
    const backgroundPage = page === 'review' ? (reviewState.sampleView ? 'samples' : 'records') : page;
    $$('[data-page]').forEach(section => section.classList.toggle('hidden', section.dataset.page !== page && section.dataset.page !== backgroundPage));
    $$('[data-nav]').forEach(link => {
      const active = link.dataset.nav === backgroundPage;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    $('#page-title').textContent = pageInfo[backgroundPage][0];
    $('#page-subtitle').textContent = pageInfo[backgroundPage][1];
    document.title = `${page === 'review' && reviewState.sampleView ? '查看样单' : pageInfo[page][0]} · 三星回单核验台`;
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
    let route = location.hash.slice(1) || 'records';
    if (route === 'progress' || route === 'quality') {
      route = 'records';
      history.replaceState(null, '', '#records');
    }
    if (reviewState.current && !/^(review|view|sample-view)\/\d+$/.test(route) && !canLeaveReview()) { history.replaceState(null, '', `#${reviewState.sampleView ? 'sample-view' : reviewState.reviewMode || 'review'}/${reviewState.current.id}`); return; }
    if (route === 'import') { showPage('records'); showImportDialog(); return; }
    if (/^(review|view|sample-view)\/\d+$/.test(route)) {
      const id = Number(route.split('/')[1]);
      const sample = route.startsWith('sample-view/');
      const mode = sample || route.startsWith('view/') ? 'view' : 'review';
      if (reviewState.current?.id === id && reviewState.reviewMode === mode && !!reviewState.sampleView === sample) showPage('review');
      else openReview(id, {mode, sample}).catch(error => {
        if (location.hash !== `#${sample ? 'sample-view' : mode}/${id}`) return;
        toast(error.message, 'danger');
        if (reviewState.current) history.replaceState(null, '', `#${reviewState.sampleView ? 'sample-view' : reviewState.reviewMode || 'review'}/${reviewState.current.id}`);
        else location.hash = sample ? 'samples' : 'records';
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
      if (route === 'records') {
        loadRecords({background: !!recordsState.recordsLoaded}).catch(error => toast(error.message, 'danger'));
        loadDailyResults({refreshRecords: false}).catch(error => toast(error.message, 'danger'));
      }
      if (route === 'samples' && loadSamples) loadSamples();
      if (route === 'seal-center' && loadSealCenter) loadSealCenter().catch(error => toast(error.message, 'danger'));
    }
  }

  function initialize() {
    window.addEventListener('hashchange', routePage);

  }

  return {initialize, showPage, routePage};
}
