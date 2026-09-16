import {escapeHtml} from './ui.mjs';
import {normalizeReviewScope, reviewScopeKey, reviewScopeRequest} from './review_scope.mjs';

export function createReviewQueue({
  environment, ui, reviewState, api, ReceiptWorkbench, canLeaveReview, openReview, showReviewEmpty, showPage
}) {
  const {location, history, URLSearchParams, AbortController, document, window} = environment;
  const {$, $$, toast} = ui;

  function currentScope() {
    if (!reviewState.reviewScope) {
      reviewState.reviewScope = normalizeReviewScope({kind:'day', filters:{
        import_date:reviewState.reviewDay || '', task_id:reviewState.reviewTask || ''}});
    }
    return reviewState.reviewScope;
  }

  function ensureReviewScope(item) {
    if (!reviewState.reviewScope) {
      const day = item?.import_date || item?.created_at?.slice(0, 10) || reviewState.reviewDay || '';
      reviewState.reviewScope = normalizeReviewScope({kind:'day', filters:{import_date:day,
        task_id:reviewState.reviewTask || ''}});
      reviewState.reviewDay = day;
    }
    reviewState.reviewKnownPending ||= new Set();
    if (item?.review_status === '待复核') reviewState.reviewKnownPending.add(item.id);
    else if (item?.id) reviewState.reviewKnownPending.delete(item.id);
    renderReviewProgress();
    return reviewState.reviewScope;
  }

  function renderReviewProgress() {
    const scope = currentScope();
    const deferred = reviewState.reviewDeferred?.size || 0;
    const processed = reviewState.reviewProcessed?.size || 0;
    const remaining = Math.max(0, (reviewState.reviewTotal || 0) - (reviewState.reviewDeferredInScope?.size || 0));
    const label = $('#review-scope-label'), progress = $('#review-session-progress');
    if (label) { label.textContent = scope.label; label.title = scope.label; }
    if (progress) {
      progress.textContent = [processed ? `已处理 ${processed} 张` : '', `待复核 ${remaining} 张`,
        deferred ? `稍后 ${deferred} 张` : ''].filter(Boolean).join(' · ');
      progress.title = '待处理仅统计当前范围；稍后为本轮保存稍后的回单数，可能包含已移出筛选范围或被其他窗口处理的回单。';
    }
  }

  function markReviewSaved(id, status) {
    reviewState.reviewProcessed ||= new Set();
    reviewState.reviewDeferred ||= new Set();
    reviewState.reviewKnownPending ||= new Set();
    reviewState.reviewDeferredInScope ||= new Set();
    const wasPending = reviewState.reviewKnownPending.has(id);
    if (status === '待复核') {
      if (!wasPending) reviewState.reviewTotal = (reviewState.reviewTotal || 0) + 1;
      reviewState.reviewProcessed.delete(id);
      reviewState.reviewDeferred.add(id);
      reviewState.reviewDeferredInScope.add(id);
      reviewState.reviewKnownPending.add(id);
    } else {
      reviewState.reviewProcessed.add(id);
      reviewState.reviewDeferred.delete(id);
      reviewState.reviewDeferredInScope.delete(id);
      reviewState.reviewKnownPending.delete(id);
      if (wasPending) reviewState.reviewTotal = Math.max(0, (reviewState.reviewTotal || 0) - 1);
    }
    renderReviewProgress();
  }

  async function loadReviewQueue(openFirst=false, {page = reviewState.reviewPage || 1} = {}) {
    reviewState.reviewQueueController?.abort();
    const controller = reviewState.reviewQueueController = new AbortController();
    const requestId = reviewState.queueRequest = (reviewState.queueRequest || 0) + 1;
    const originHash = location.hash, intent = reviewState.reviewIntent || 0;
    const scope = currentScope(), scopeKey = reviewScopeKey(scope);
    if (scopeKey !== reviewState.reviewScopeKey) page = 1;
    reviewState.reviewScopeKey = scopeKey;
    let result;
    try {
      result = await api(reviewScopeRequest(scope, page, URLSearchParams, reviewState.reviewDeferred), {signal:controller.signal});
    } catch (error) {
      if (requestId !== reviewState.queueRequest || error.name === 'AbortError') return null;
      throw error;
    }
    if (requestId !== reviewState.queueRequest || location.hash !== originHash || reviewScopeKey(currentScope()) !== scopeKey) return null;
    const pages = Math.max(1, Math.ceil(result.total / result.page_size));
    if (result.page > pages) return loadReviewQueue(openFirst, {page:pages});
    const rows = result.items;
    reviewState.reviewKnownPending ||= new Set();
    rows.forEach(row => reviewState.reviewKnownPending.add(row.id));
    reviewState.reviewQueue = rows; reviewState.reviewDay = scope.filters.import_date || '';
    reviewState.reviewPage = result.page; reviewState.reviewTotal = result.total;
    reviewState.reviewDeferredInScope = new Set(result.deferred_in_scope_ids || []);
    $('#review-scope').textContent = `${result.total} 张待复核`;
    renderReviewProgress();
    $('#review-page-info').textContent = `第 ${result.page} / ${pages} 页`;
    $('#review-page-prev').disabled = result.page <= 1;
    $('#review-page-next').disabled = result.page >= pages;
    $('#review-queue').innerHTML = rows.map(row => `<button type="button" class="review-queue-item ${reviewState.current?.id === row.id ? 'active' : ''}" data-queue-review="${row.id}"><strong>${escapeHtml(row.filename)}</strong><small>${escapeHtml(row.fields?.['客户名称'] || '')}</small><small>${escapeHtml(ReceiptWorkbench.issues(row)[0]?.message || '等待人工核对')}${reviewState.reviewDeferred.has(row.id) ? ' · 稍后处理' : ''}</small></button>`).join('') || '<p class="empty-state">当前范围没有待复核回单</p>';
    $$('[data-queue-review]').forEach(button => button.addEventListener('click', () => openReview(Number(button.dataset.queueReview)).catch(error=>toast(error.message,'danger'))));
    if (openFirst) await openNextReview(result, undefined, intent, originHash);
    return result;
  }

  async function openNextReview(result, currentId, intent = reviewState.reviewIntent || 0, originHash = location.hash) {
    const visited = new Set();
    while (result && !visited.has(result.page)) {
      if ((reviewState.reviewIntent || 0) !== intent || location.hash !== originHash) return;
      const skipped = new Set([...reviewState.reviewDeferred, ...(reviewState.reviewProcessed || [])]);
      const next = ReceiptWorkbench.nextReview(result.items, currentId, skipped);
      if (next) return openReview(next.id);
      visited.add(result.page);
      const pages = Math.max(1, Math.ceil(result.total / result.page_size));
      const nextPage = result.page < pages ? result.page + 1 : 1;
      if (visited.has(nextPage)) break;
      result = await loadReviewQueue(false, {page:nextPage});
    }
    if (result && (reviewState.reviewIntent || 0) === intent && location.hash === originHash) {
      showReviewEmpty(); history.replaceState(null, '', '#review'); renderReviewProgress();
    }
  }

  async function continueReview() {
    const old = reviewState.current?.id, intent = reviewState.reviewIntent || 0, originHash = location.hash;
    const result = await loadReviewQueue(false);
    if (result) await openNextReview(result, old, intent, originHash);
  }

  async function startReviewScope(scope, {recordId} = {}) {
    const nextScope = normalizeReviewScope(scope);
    if (!canLeaveReview()) return false;
    if (document?.body.dataset.activePage !== 'review') {
      reviewState.reviewOrigin = {hash: /^#review/.test(location.hash) ? '#progress' : location.hash || '#progress', scroll:window?.scrollY || 0};
    }
    reviewState.reviewIntent = (reviewState.reviewIntent || 0) + 1;
    reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
    reviewState.reviewSessionId = (reviewState.reviewSessionId || 0) + 1;
    reviewState.reviewScope = nextScope; reviewState.reviewScopeKey = '';
    reviewState.reviewDeferred = new Set(); reviewState.reviewProcessed = new Set();
    reviewState.reviewDeferredInScope = new Set();
    reviewState.reviewKnownPending = new Set();
    reviewState.reviewPage = 1; reviewState.reviewTotal = 0;
    reviewState.reviewTask = nextScope.filters.task_id || '';
    reviewState.reviewDay = nextScope.filters.import_date || '';
    reviewState.current = null; showReviewEmpty({loading: true});
    if (document?.body.dataset.activePage === 'review' || /^#review/.test(location.hash)) history.replaceState(null, '', '#review');
    else (history.pushState || history.replaceState).call(history, null, '', '#review');
    showPage?.('review'); renderReviewProgress();
    const intent = reviewState.reviewIntent;
    try {
      const result = await loadReviewQueue(false);
      if (!result || reviewState.reviewIntent !== intent) return false;
      // A row can be outside the first queue page or already reviewed.
      if (recordId && (nextScope.kind !== 'selected' || nextScope.ids.includes(recordId))) await openReview(recordId);
      else await openNextReview(result, undefined, intent, location.hash);
      return true;
    } catch (error) {
      // openReview advances the intent too; session identity guards the detail
      // failure while the route check prevents reopening a dismissed dialog.
      if (reviewState.reviewScope === nextScope && /^#review/.test(location.hash) && !reviewState.current) showReviewEmpty({error: error.message});
      throw error;
    }
  }

  async function refreshReviewScope(clearDeferred=false) {
    if (clearDeferred) {
      if (!canLeaveReview()) return;
      reviewState.reviewIntent = (reviewState.reviewIntent || 0) + 1;
      reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
      reviewState.reviewDeferred = new Set(); reviewState.reviewPage = 1;
      reviewState.reviewDeferredInScope = new Set();
      await loadReviewQueue(true);
    } else await loadReviewQueue(!reviewState.current);
  }

  function initialize() {
    const safely = action => () => Promise.resolve().then(action).catch(error => toast(error.message, 'danger'));
    $('#review-refresh').addEventListener('click', safely(() => refreshReviewScope()));
    $('#review-revisit').addEventListener('click', safely(() => refreshReviewScope(true)));
    $('#review-page-prev').addEventListener('click', safely(() => loadReviewQueue(false, {page:Math.max(1, reviewState.reviewPage - 1)})));
    $('#review-page-next').addEventListener('click', safely(() => loadReviewQueue(false, {page:reviewState.reviewPage + 1})));
    $('#toggle-review-queue').addEventListener('click', event => {
      const collapsed = $('.review-workspace').classList.toggle('queue-collapsed');
      event.currentTarget.setAttribute('aria-expanded', String(!collapsed));
    });
  }

  return {initialize, loadReviewQueue, openNextReview, continueReview, refreshReviewScope,
    startReviewScope, markReviewSaved, renderReviewProgress, ensureReviewScope};
}
