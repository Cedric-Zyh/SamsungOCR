/** Filter request lifecycle, cancellation and stale-response protection. */
export function createRecordQuery({environment, ui, recordsState, services, searchState,
  syncColumnFilters, renderFilterChips, closeMoreMenu, updateSelectionToolbar,
  showRecordEmptyState, renderRecords}) {
  const {FormData, URLSearchParams, AbortController, setTimeout, clearTimeout} = environment;
  const {$, $$, toast} = ui;
  function scheduleRecordSearch(immediate = false) {
    clearTimeout(searchState.timer);
    recordsState.recordPage = 1;
    recordsState.recordController?.abort();
    recordsState.recordRequest = (recordsState.recordRequest || 0) + 1;
    $('#record-filter-status').textContent = '正在筛选…';
    recordsState.selected.clear(); $('#select-all').checked = false; $('#select-all').disabled = true; updateSelectionToolbar();
    renderFilterChips();
    $('#records-body').inert = true;
    $('#records-body').setAttribute('aria-busy', 'true');
    if (!searchState.composing) searchState.timer = setTimeout(loadRecords, immediate ? 0 : 150);
  }
  async function loadRecords({background = false, page = recordsState.recordPage, scrollToTable = false} = {}) {
    if (background && (searchState.composing || recordsState.recordLoading || searchState.timer)) return;
    clearTimeout(searchState.timer);
    searchState.timer = null;
    recordsState.recordController?.abort();
    const controller = recordsState.recordController = new AbortController();
    $('#filters [name=task_id]').value = recordsState.batchFilter;
    $$('[data-filter]').forEach(button => button.classList.toggle('active', button.dataset.filter === $('#filters').elements.namedItem('overall').value));
    syncColumnFilters();
    renderFilterChips();
    const params = new URLSearchParams(new FormData($('#filters')));
    const filterKey = params.toString();
    if (filterKey !== recordsState.recordFilterKey) { page = 1; background = false; }
    recordsState.recordFilterKey = filterKey;
    params.set('page', String(page || 1)); params.set('page_size', String(recordsState.recordPageSize));

    closeMoreMenu();
    const requestId = recordsState.recordRequest = (recordsState.recordRequest || 0) + 1;
    recordsState.recordLoading = true; recordsState.recordAttemptAt = Date.now();
    if (!background) { recordsState.selected.clear(); updateSelectionToolbar(); }
    const body = $('#records-body');
    if (!background) { $('#select-all').disabled = true; body.inert = true; }
    body.setAttribute('aria-busy', 'true');
    $('#records-page-prev').disabled = $('#records-page-next').disabled = true;
    $('#record-filter-status').textContent = background ? '正在更新记录…' : '正在筛选…';
    try {
      const result = await (services.records.list(params, {signal: controller.signal}));
      if (requestId !== recordsState.recordRequest) return;
      const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
      if (result.page > lastPage) { recordsState.recordLoading = false; return await loadRecords({background, page:lastPage, scrollToTable}); }
      recordsState.records = result.items; recordsState.recordPage = result.page; recordsState.recordTotal = result.total;
      recordsState.recordsLoaded = true; recordsState.recordsStale = false;
      const visibleIds = new Set(recordsState.records.map(record => Number(record.id)));
      recordsState.selected = new Set([...recordsState.selected].filter(id => visibleIds.has(id)));
      $('#record-count').textContent = `共 ${result.total} 张`;
      $('#records-page-info').textContent = `第 ${result.page} / ${lastPage} 页 · 本页 ${result.items.length} 张`;
      $('#records-page-prev').disabled = result.page <= 1;
      $('#records-page-next').disabled = result.page >= lastPage;
    } catch (error) {
      if (requestId !== recordsState.recordRequest || error.name === 'AbortError') return;
      $('#record-filter-status').classList.remove('sr-only');
      $('#record-filter-status').textContent = background ? '记录暂未更新，当前选择已保留' : '未能完成筛选，可在下方重新加载';
      if (!background) {
        recordsState.records = []; recordsState.recordsLoaded = false; recordsState.recordTotal = 0;
        $('#record-count').textContent = '数量未更新';
        $('#records-page-info').textContent = '加载失败';
        showRecordEmptyState({failed: true, message: error.message});
      } else {
        const lastPage = Math.max(1, Math.ceil(recordsState.recordTotal / recordsState.recordPageSize));
        $('#records-page-prev').disabled = recordsState.recordPage <= 1;
        $('#records-page-next').disabled = recordsState.recordPage >= lastPage;
      }
      toast(error.message, 'danger'); return;
    } finally {
      if (requestId === recordsState.recordRequest) { recordsState.recordLoading = false; body.inert = false; body.removeAttribute('aria-busy'); $('#select-all').disabled = !recordsState.records.length; updateSelectionToolbar(); }
    }
    renderRecords({background, scrollToTable});
  }

  return {loadRecords, scheduleRecordSearch};
}
