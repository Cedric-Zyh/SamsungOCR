import {localToday, filenameParts, statusClass, checkStatusLabel, escapeHtml} from './ui.mjs';
import {activeFilterEntries, recordReviewScope} from './record_filters.mjs';
import {createImportCalendar} from './import_calendar.mjs';
import {providerErrors} from './provider_errors.mjs';

export function createRecords({
  environment, ui, ReceiptWorkbench, recordsState, api, startReviewScope, retryOne,
  refreshVisibleResults, setBatchStep, openProcessHistory = () => {}
}) {
  const {document, window, location, FormData, URLSearchParams, AbortController, setTimeout, clearTimeout} = environment;
  const {$, $$, toast} = ui;
  let recordSearchTimer, recordResetTimer, recordComposing = false;
  let tablePopover, tablePopoverAnchor;
  let bulkActionPending = false;
  const calendar = createImportCalendar({environment, ui, api, prefix: 'calendar', popup: '#import-calendar',
    readDate: () => $('#import-date').value,
    selectDate: day => { setImportDate(day); changeImportDate(); }});

  function currentFilters() {
    const filters = Object.fromEntries(new FormData($('#filters')));
    filters.task_id = recordsState.batchFilter || '';
    return filters;
  }
  function getReviewScope(kind = 'filtered') {
    return recordReviewScope(currentFilters(), kind, [...recordsState.selected]);
  }
  async function startRecordsReview(kind = 'filtered', recordId) {
    if (recordComposing || recordsState.recordLoading || recordSearchTimer) {
      toast('正在更新筛选，请稍候再开始复核', 'warning');
      return;
    }
    if (kind === 'selected' && !recordsState.selected.size) {
      toast('请先勾选需要复核的回单', 'warning');
      return;
    }
    try { await startReviewScope(getReviewScope(kind), {recordId}); }
    catch (error) { toast(error.message, 'danger'); }
  }
  function renderFilterChips() {
    const entries = activeFilterEntries(currentFilters());
    const container = $('#active-filters');
    container.innerHTML = entries.map(({key, label, value}) =>
      `<button type="button" class="filter-chip" data-remove-filter="${key}" aria-label="移除${escapeHtml(label)}：${escapeHtml(value)}"><span>${escapeHtml(label)}：${escapeHtml(value)}</span><span aria-hidden="true">×</span></button>`).join('');
    container.classList.toggle('hidden', !entries.length);
  }
  function removeFilter(key) {
    const input = $('#filters').elements.namedItem(key);
    if (!input) return;
    input.value = '';
    if (key === 'task_id') {
      recordsState.batchFilter = '';
      $('#batch-scope').classList.add('hidden');
    }
    syncColumnFilters();
    renderFilterChips();
    scheduleRecordSearch(true);
  }
  function setDensity(compact) {
    recordsState.compact = !!compact;
    $('.records-section').classList.toggle('is-compact', !!compact);
    $('#records-density-toggle').setAttribute('aria-pressed', String(!!compact));
    $('#records-density-toggle').textContent = compact ? '舒适显示' : '紧凑显示';
    try { environment.localStorage?.setItem('receipt.records.compact', String(!!compact)); } catch (_) { /* The setting remains usable when browser storage is disabled. */ }
  }

  function clearAdditionalFilters() {
    const form = $('#filters');
    for (const {key} of activeFilterEntries(currentFilters())) {
      if (key !== 'task_id') {
        const input = form.elements.namedItem(key);
        if (input) input.value = '';
      }
    }
    syncColumnFilters();
    scheduleRecordSearch(true);
  }

  async function setPageSize(value) {
    const size = Number(value);
    if (![25, 50, 100].includes(size) || recordsState.recordLoading) return;
    recordsState.recordPageSize = size;
    $('#records-page-size').value = String(size);
    try { environment.localStorage?.setItem('receipt.records.page-size', String(size)); } catch (_) { /* The current session still uses the chosen size. */ }
    await loadRecords({page: 1, scrollToTable: true});
  }

  function showRecordEmptyState({failed = false, message = ''} = {}) {
    const filters = currentFilters();
    const hasConditions = activeFilterEntries(filters).some(({key}) => key !== 'task_id');
    const title = failed ? '记录加载失败' : hasConditions ? '没有符合筛选条件的回单' : '当前范围还没有回单';
    const detail = failed ? message || '连接暂时不可用，请稍后重试。'
      : hasConditions ? '可清除附加筛选后重试，导入日期和任务范围会保留。'
      : filters.task_id ? '该任务暂无回单，可切换导入日期查看其他记录。' : '所选日期暂无导入记录，可切换日期查看。';
    const action = failed ? '<button type="button" class="secondary-button" data-record-retry>重新加载</button>'
      : hasConditions ? '<button type="button" class="secondary-button" data-clear-record-filters>清除附加筛选</button>'
      : '<button type="button" class="secondary-button" data-record-date>选择其他日期</button>';
    $('#records-body').innerHTML = `<tr><td colspan="8" class="empty-state"><div class="records-empty"><strong>${title}</strong><p>${escapeHtml(detail)}</p><div class="records-empty-actions">${action}</div></div></td></tr>`;
  }

  function setImportDate(value) {
    $('#import-date').value = $('#import-date').defaultValue = value || localToday();
    calendar.sync();
  }
  function openDayRecords(day) {
    const match = typeof day === 'string' && day.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const [year, month, date] = match ? match.slice(1).map(Number) : [];
    const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (!match || year < 1 || month < 1 || month > 12 || date < 1 || date > days[month - 1]) {
      toast('请先选择有效的工作台日期', 'warning');
      return false;
    }

    clearTimeout(recordSearchTimer);
    clearTimeout(recordResetTimer);
    recordSearchTimer = recordResetTimer = null;
    recordComposing = false;
    recordsState.recordController?.abort();
    recordsState.recordRequest = (recordsState.recordRequest || 0) + 1;
    recordsState.recordLoading = false;
    recordsState.recordsLoaded = false;
    recordsState.recordsStale = true;
    recordsState.recordFilterKey = '';
    recordsState.recordPage = 1;
    recordsState.recordTotal = 0;
    recordsState.records = [];
    recordsState.batchFilter = '';
    recordsState.selected.clear();

    // Clear values directly: form.reset() schedules an additional query.
    const form = $('#filters');
    for (const [key] of new FormData(form)) {
      const input = form.elements.namedItem(key);
      if (input) input.value = key === 'text_match' ? 'prefix' : '';
    }
    setImportDate(day);
    calendar.close();
    closeTablePopover();
    $('#batch-scope').classList.add('hidden');
    $$('[data-filter]').forEach(button => button.classList.toggle('active', button.dataset.filter === ''));
    syncColumnFilters();
    renderFilterChips();
    updateSelectionToolbar();
    $('#select-all').disabled = true;
    $('#record-count').textContent = '';
    $('#records-page-info').textContent = '正在加载…';
    $('#records-page-prev').disabled = $('#records-page-next').disabled = true;
    $('#record-filter-status').textContent = '正在加载当天全部回单…';
    const body = $('#records-body');
    body.innerHTML = '<tr><td colspan="8" class="empty-state">正在加载当天全部回单…</td></tr>';
    body.inert = true;
    body.setAttribute('aria-busy', 'true');
    location.hash = 'records';
    return true;
  }
  function changeImportDate() {
    setImportDate($('#import-date').value);
    recordsState.batchFilter = '';
    $('#filters [name="task_id"]').value = '';
    $('#batch-scope').classList.add('hidden');
    loadRecords().catch(error => toast(error.message, 'danger'));
  }

  function setColumnFilterOpen(column, open) {
    column.classList.toggle('is-open', open);
    $('.column-filter-toggle', column).setAttribute('aria-expanded', String(open));
  }
  function syncColumnFilters() {
    $$('[data-filter-popover]').forEach(button => {
      const active = $$('input,select', $(`#filter-${button.dataset.filterPopover}`)).some(input => input.value);
      button.classList.toggle('has-value', active);
      button.title = active ? '已应用筛选，点击调整' : '点击筛选';
    });
    $$('[data-column-filter]').forEach(column => {
      const input = $('.column-filter-input', column), button = $('.column-filter-toggle', column);
      column.classList.toggle('has-value', !!input.value);
      $('span', button).textContent = input.value ? `${column.dataset.title}：${input.value}` : column.dataset.title;
      button.title = input.value ? `${column.dataset.title}：${input.value}` : column.dataset.title;
      if (input.value) setColumnFilterOpen(column, true);
      else if (!column.contains(document.activeElement)) setColumnFilterOpen(column, false);
    });
  }

  function closeTablePopover(restoreFocus = false) {
    if (!tablePopover) return;
    tablePopover.classList.add('hidden');
    tablePopoverAnchor?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) tablePopoverAnchor?.focus();
    tablePopover = tablePopoverAnchor = null;
  }
  function showTablePopover(popover, anchor) {
    if (tablePopover === popover && tablePopoverAnchor === anchor) return closeTablePopover(true);
    closeTablePopover();
    tablePopover = popover; tablePopoverAnchor = anchor;
    popover.classList.remove('hidden'); anchor.setAttribute('aria-expanded', 'true');
    const rect = anchor.getBoundingClientRect();
    popover.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - popover.offsetWidth - 12))}px`;
    popover.style.top = `${Math.max(12, rect.bottom + popover.offsetHeight + 8 > window.innerHeight ? rect.top - popover.offsetHeight - 8 : rect.bottom + 8)}px`;
    $('input,select,button', popover)?.focus();
  }

  function scheduleRecordSearch(immediate = false) {
    clearTimeout(recordSearchTimer);
    recordsState.recordPage = 1;
    recordsState.recordController?.abort();
    recordsState.recordRequest = (recordsState.recordRequest || 0) + 1;
    $('#record-filter-status').textContent = '正在筛选…';
    recordsState.selected.clear(); $('#select-all').checked = false; $('#select-all').disabled = true; updateSelectionToolbar();
    renderFilterChips();
    $('#records-body').inert = true;
    $('#records-body').setAttribute('aria-busy', 'true');
    if (!recordComposing) recordSearchTimer = setTimeout(loadRecords, immediate ? 0 : 150);
  }
  function isRecordFilter(target) { return target.form === $('#filters') && !['hidden','reset','submit'].includes(target.type); }

  async function loadRecords({background = false, page = recordsState.recordPage, scrollToTable = false} = {}) {
    if (background && (recordComposing || recordsState.recordLoading || recordSearchTimer)) return;
    clearTimeout(recordSearchTimer);
    recordSearchTimer = null;
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

    if (tablePopover?.id === 'record-more-menu') closeTablePopover();
    const requestId = recordsState.recordRequest = (recordsState.recordRequest || 0) + 1;
    recordsState.recordLoading = true; recordsState.recordAttemptAt = Date.now();
    if (!background) { recordsState.selected.clear(); updateSelectionToolbar(); }
    const body = $('#records-body');
    if (!background) { $('#select-all').disabled = true; body.inert = true; }
    body.setAttribute('aria-busy', 'true');
    $('#records-page-prev').disabled = $('#records-page-next').disabled = true;
    $('#records-page-size').disabled = true;
    $('#record-filter-status').textContent = background ? '正在更新记录…' : '正在筛选…';
    try {
      const result = await api(`/api/results?${params}`, {signal: controller.signal});
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
      if (requestId === recordsState.recordRequest) { recordsState.recordLoading = false; body.inert = false; body.removeAttribute('aria-busy'); $('#select-all').disabled = !recordsState.records.length; $('#records-page-size').disabled = false; updateSelectionToolbar(); }
    }
    $('#record-filter-status').textContent = `已加载 ${recordsState.records.length} 张回单`;
    $('#record-filter-status').classList.add('sr-only');
    if (!recordsState.records.length) showRecordEmptyState();
    else {
      const table = $('.record-filter-table');
      const previousScroll = {top: table.scrollTop, left: table.scrollLeft};
      const active = document.activeElement;
      const keepFocus = background && body.contains?.(active);
      const focusTarget = !keepFocus ? '' : active.matches?.('.record-select') ? `.record-select[value="${Number(active.value)}"]`
        : active.dataset.openReview ? `[data-open-review="${Number(active.dataset.openReview)}"].${active.classList.contains('record-file-link') ? 'record-file-link' : 'row-review'}`
        : active.dataset.moreRecord ? `[data-more-record="${Number(active.dataset.moreRecord)}"]` : '';
      body.innerHTML = recordsState.records.map(recordRow).join('');
      if (focusTarget) $(focusTarget, body)?.focus({preventScroll: true});
      if (!scrollToTable) { table.scrollTop = previousScroll.top; table.scrollLeft = previousScroll.left; }
      $$('.record-select', body).forEach(input => input.addEventListener('change', () => toggleSelected(Number(input.value), input.checked)));
      $$('[data-open-review]', body).forEach(button => button.addEventListener('click', () => startRecordsReview('filtered', Number(button.dataset.openReview))));
      $$('[data-process-result]', body).forEach(button => button.addEventListener('click', () => openProcessHistory({
        resultId: Number(button.dataset.processResult),
        filename: button.dataset.filename || ''
      })));
      $$('[data-delete-row]', body).forEach(button => button.addEventListener('click', () => deleteRecords([Number(button.dataset.deleteRow)])));
      $$('[data-retry-row]', body).forEach(button => button.addEventListener('click', () => retryOne(Number(button.dataset.retryRow), button)));
    }
    if (scrollToTable) {
      const table = $('.record-filter-table');
      table.scrollTo?.({top: 0, behavior: 'instant'});
      const bounds = table.getBoundingClientRect?.();
      if (bounds && (bounds.top < 0 || bounds.top > window.innerHeight - 120)) {
        table.scrollIntoView?.({block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'instant' : 'smooth'});
      }
    }
  }

  function recordRow(item) {
    const fields = item.fields || {}, date = item.date_check || {};
    const providerFailed = ReceiptWorkbench.hasProviderFailure(item);
    const file = filenameParts(item.filename);
    const timestamp = (item.updated_at || item.created_at || '').replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '').split(' ');
    const pageInfo = item.page_group?.page_count > 1 ? `第 ${Number(item.page_index || 0) + 1} 页 / 共 ${item.page_group.page_count} 页` : '';
    const expected = fields['要求到货'] || date.required || '';
    const warehouseReceiver = fields['仓库接收人'] || item.internal_fields?.['仓库接收人'] || '';
    const warehouseContact = fields['仓库联系人'] || item.internal_fields?.['仓库联系人'] || '';
    const signatureCheck = ReceiptWorkbench.signatureCheck(item);
    const signatureStatus = signatureCheck.status || (warehouseReceiver && warehouseContact
      ? (warehouseReceiver.replace(/\s+/g, '') === warehouseContact.replace(/\s+/g, '') ? '匹配' : '不匹配')
      : '未识别');
    const dateDifferent = date.actual && date.status === '不匹配';
    const order = fields['客户订单号'] || item.internal_fields?.['客户订单号'] || '';
    const fileMeta = [order ? `订单 ${order}` : file.folder, pageInfo].filter(Boolean).join(' · ');
    const verdict = providerFailed ? '识别失败' : item.final_result || item.overall || '';
    const verdictLabel = ({'需人工复核': '待复核', '通过': '已通过'})[verdict] || verdict || '—';
    const redundantReview = providerFailed || item.review_status === '无需复核'
      || (verdict === '需人工复核' && item.review_status === '待复核')
      || (verdict === '通过' && item.review_status === '确认通过')
      || (verdict === '不通过' && item.review_status === '确认不通过');
    const reviewDetail = item.review_status && !redundantReview ? `<small>${escapeHtml(item.review_status)}</small>` : '';
    const verdictTitle = providerFailed ? verdict : [verdict, item.review_status].filter(Boolean).join(' · ');
    const errors = [...new Set([item.error_message, ...providerErrors(item)].filter(Boolean))];
    const primaryAction = providerFailed
      ? `<button type="button" class="mini-button row-review" data-retry-row="${item.id}">重试</button>`
      : `<button type="button" class="mini-button row-review" data-open-review="${item.id}">${item.review_status === '待复核' ? '复核' : '查看'}</button>`;
    const processAction = `<button type="button" class="mini-button row-process" data-process-result="${item.id}" data-filename="${escapeHtml(item.filename)}" aria-label="查看 ${escapeHtml(file.name)} 的流程记录">流程</button>`;
    return `<tr class="${providerFailed ? 'failed-row' : item.review_status === '待复核' ? 'pending-row' : ''}">
      <td><input class="record-select" type="checkbox" value="${item.id}" ${recordsState.selected.has(Number(item.id)) ? 'checked' : ''} aria-label="选择 ${escapeHtml(item.filename)}"></td>
      <td class="file-cell" title="${escapeHtml(item.filename)}"><button type="button" class="record-file-link" data-open-review="${item.id}" aria-label="查看回单 ${escapeHtml(item.filename)}"><strong>${escapeHtml(file.name)}</strong></button>${fileMeta ? `<small class="record-file-meta" title="${escapeHtml(fileMeta)}">${escapeHtml(fileMeta)}</small>` : ''}${errors.map(message => `<small class="error-text" title="${escapeHtml(message)}">${escapeHtml(message)}</small>`).join('')}</td>
      <td class="customer-cell" title="${escapeHtml(fields['客户名称'] || '')}"><span>${escapeHtml(fields['客户名称'] || '—')}</span></td>
      <td class="signature-cell" title="仓库联系人：${escapeHtml(warehouseContact)}；仓库接收人：${escapeHtml(warehouseReceiver)}"><div><span><em>仓库联系人</em>${escapeHtml(warehouseContact || '—')}</span><small><span><em>仓库接收人</em>${escapeHtml(warehouseReceiver || '未识别')}</span>${checkStatusLabel(signatureStatus)}</small></div></td>
      <td><div class="date-cell"><div><span><em>要求</em>${escapeHtml(expected || '—')}</span><small class="date-actual${dateDifferent ? ' date-difference' : ''}"><span><em>签收</em>${escapeHtml(date.actual || '未识别')}</span>${checkStatusLabel(ReceiptWorkbench.checkStatus(item, 'date'))}</small></div></div></td>
      <td>${checkStatusLabel(ReceiptWorkbench.checkStatus(item, 'seal'))}</td>
      <td class="verdict-cell" title="${escapeHtml(verdictTitle)}"><span class="pill ${statusClass(verdict)}">${escapeHtml(verdictLabel)}</span>${reviewDetail}</td>
      <td class="time-cell"><span>${escapeHtml(timestamp[0] || '—')}</span><small>${escapeHtml(timestamp[1] || '')}</small></td>
      <td><div class="row-actions">${primaryAction}${processAction}<button class="mini-button row-more" data-more-record="${item.id}" aria-label="${escapeHtml(file.name)} 更多操作" aria-expanded="false" aria-controls="record-more-menu">···</button></div></td></tr>`;
  }

  function toggleSelected(id, checked) { checked ? recordsState.selected.add(id) : recordsState.selected.delete(id); updateSelectionToolbar(); }
  function selectCurrentPage(checked) {
    recordsState.records.forEach(record => checked ? recordsState.selected.add(Number(record.id)) : recordsState.selected.delete(Number(record.id)));
    $$('.record-select').forEach(input => { input.checked = checked; });
    updateSelectionToolbar();
  }
  function updateSelectionToolbar() {
    const selected = recordsState.records.filter(item => recordsState.selected.has(Number(item.id)));
    const unsafeCount = selected.filter(item => !canBulkConfirm(item)).length;
    const busy = !!recordsState.recordLoading || bulkActionPending;
    $('#selection-toolbar').classList.toggle('hidden', !recordsState.selected.size);
    $('#selection-toolbar').setAttribute('aria-busy', String(bulkActionPending));
    $('#selection-count').textContent = `本页已选 ${recordsState.selected.size} 张`;
    $('#selection-readiness').setAttribute('data-tone', unsafeCount ? 'warning' : '');
    $('#selection-readiness').textContent = bulkActionPending ? '正在处理选中回单…' : unsafeCount ? `${unsafeCount} 张需逐张确认日期或印章` : selected.length ? '选中回单均可批量通过' : '';
    $('#bulk-pass').disabled = busy || !selected.length || unsafeCount > 0;
    $('#bulk-pass').title = unsafeCount ? `${unsafeCount} 张日期或印章证据不完整，请先逐张复核` : '批量通过当前页选中的回单';
    for (const id of ['bulk-pending', 'bulk-delete', 'clear-record-selection']) $('#'+id).disabled = busy || !selected.length;
    $('#review-selected-records').textContent = `复核选中（${recordsState.selected.size}）`;
    $('#review-selected-records').disabled = !recordsState.selected.size || busy;
    $('#review-filtered-records').disabled = !!recordsState.recordLoading || !!recordComposing;
    $('#select-all').checked = !!recordsState.records.length && recordsState.records.every(record => recordsState.selected.has(Number(record.id)));
    $('#select-all').indeterminate = recordsState.selected.size > 0 && !$('#select-all').checked;
  }

  function canBulkConfirm(item) {
    const date = item.date_check || {}, seal = item.seal_check || {};
    return !!date.actual && date.status === '匹配' && date.reliable === true && !!seal.recognized && seal.status === '匹配' && seal.reliable === true;
  }

  async function bulkReview(reviewStatus, finalResult) {
    if (bulkActionPending || recordsState.recordLoading) return;
    if (!recordsState.selected.size) return toast('请先勾选回单', 'warning');
    const ids = [...recordsState.selected];
    if (reviewStatus === '确认通过') {
      const unsafe = recordsState.records.filter(item => recordsState.selected.has(Number(item.id))).filter(item => !canBulkConfirm(item));
      if (unsafe.length) return toast(`有 ${unsafe.length} 张日期或印章证据不完整，不能批量确认通过`, 'danger');
    }
    bulkActionPending = true; updateSelectionToolbar();
    try {
      await api('/api/results/bulk-review', {method: 'POST', json: {ids, review_status: reviewStatus, final_result: finalResult}});
      toast(`已批量更新 ${ids.length} 张回单`, 'success'); await refreshVisibleResults();
    } catch (error) {
      toast(error.message, 'danger');
    } finally { bulkActionPending = false; updateSelectionToolbar(); }
  }

  async function deleteRecords(ids) {
    if (bulkActionPending || recordsState.recordLoading) return;
    if (!ids.length) return toast('请先选择回单', 'warning');
    bulkActionPending = true; updateSelectionToolbar();
    try {
      await api('/api/results/delete', {method:'POST', json:{ids}});
      toast('回单记录已删除', 'success');
      await refreshVisibleResults();
    } catch (error) { toast(error.message, 'danger'); }
    finally { bulkActionPending = false; updateSelectionToolbar(); }
  }

  function initialize() {
    $('#import-date').value = $('#import-date').defaultValue = localToday();
    calendar.sync();
    let compact = false;
    try { compact = environment.localStorage?.getItem('receipt.records.compact') === 'true'; } catch (_) { /* Keep the default density. */ }
    setDensity(compact);
    try {
      const savedSize = Number(environment.localStorage?.getItem('receipt.records.page-size'));
      if ([25, 50, 100].includes(savedSize)) recordsState.recordPageSize = savedSize;
    } catch (_) { /* Keep the default page size. */ }
    $('#records-page-size').value = String(recordsState.recordPageSize);
    $('#records-page-size').addEventListener('change', event => setPageSize(event.target.value));
    $('#clear-record-selection').addEventListener('click', () => selectCurrentPage(false));
    $('#records-density-toggle').addEventListener('click', () => setDensity(!recordsState.compact));
    $('#active-filters').addEventListener('click', event => {
      const button = event.target.closest('[data-remove-filter]');
      if (button) removeFilter(button.dataset.removeFilter);
    });
    $('#review-filtered-records').addEventListener('click', () => startRecordsReview('filtered'));
    $('#review-selected-records').addEventListener('click', () => startRecordsReview('selected'));
    $('#review-day-records').addEventListener('click', () => startRecordsReview('day'));

    $('#import-date').addEventListener('change', changeImportDate);
    $('#import-today').addEventListener('click', () => { setImportDate(localToday()); changeImportDate(); });

    $$('[data-column-filter]').forEach(column => {
      const input = $('.column-filter-input', column), button = $('.column-filter-toggle', column);
      button.addEventListener('click', () => {
        const open = !column.classList.contains('is-open');
        setColumnFilterOpen(column, open);
        if (open) input.focus();
      });
      column.addEventListener('focusout', () => setTimeout(() => {
        if (!input.value && !column.contains(document.activeElement)) setColumnFilterOpen(column, false);
      }));
      column.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !event.isComposing) {
          event.preventDefault(); event.stopPropagation(); setColumnFilterOpen(column, false); button.focus();
        }
      });
    });

    $$('[data-filter-popover]').forEach(button => button.addEventListener('click', () => showTablePopover($(`#filter-${button.dataset.filterPopover}`), button)));
    $$('[data-close-popover]').forEach(button => button.addEventListener('click', () => closeTablePopover(true)));
    document.addEventListener('click', event => {
      $$('[data-page="records"] .records-review-more[open]').forEach(menu => {
        if (!menu.contains(event.target) || event.target.closest('button')) menu.open = false;
      });
      if (tablePopover && !tablePopover.contains(event.target) && !tablePopoverAnchor?.contains(event.target)) closeTablePopover();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') $$('[data-page="records"] .records-review-more[open]').forEach(menu => { menu.open = false; $('summary', menu)?.focus(); });
      if (event.key === 'Escape' && tablePopover) { event.preventDefault(); closeTablePopover(true); }
    });
    window.addEventListener('resize', () => closeTablePopover());
    window.addEventListener('hashchange', () => closeTablePopover());
    document.addEventListener('scroll', event => { if (tablePopover && !tablePopover.contains(event.target)) closeTablePopover(); }, true);
    $('#records-body').addEventListener('click', event => {
      if (event.target.closest('[data-record-retry]')) return loadRecords();
      if (event.target.closest('[data-clear-record-filters]')) return clearAdditionalFilters();
      if (event.target.closest('[data-record-date]')) {
        event.stopPropagation();
        $('#calendar-toggle').click();
        return;
      }
      const button = event.target.closest('[data-more-record]');
      if (!button) return;
      $('#record-more-menu').dataset.recordId = button.dataset.moreRecord;
      showTablePopover($('#record-more-menu'), button);
    });
    $('[data-menu-retry]').addEventListener('click', () => {
      const id = Number($('#record-more-menu').dataset.recordId);
      closeTablePopover(); retryOne(id);
    });
    $('[data-menu-delete]').addEventListener('click', () => {
      const id = Number($('#record-more-menu').dataset.recordId);
      closeTablePopover(); deleteRecords([id]);
    });

    document.addEventListener('compositionstart', event => { if (isRecordFilter(event.target)) { recordComposing = true; scheduleRecordSearch(); } });
    document.addEventListener('compositionend', event => { if (isRecordFilter(event.target)) { recordComposing = false; scheduleRecordSearch(); } });
    document.addEventListener('input', event => { if (isRecordFilter(event.target)) scheduleRecordSearch(event.target.type === 'date' || event.target.tagName === 'SELECT'); });
    document.addEventListener('change', event => { if (isRecordFilter(event.target) && ['SELECT', 'INPUT'].includes(event.target.tagName) && event.target.type !== 'search') scheduleRecordSearch(true); });
    $('#filters').addEventListener('submit', event => { event.preventDefault(); if (!recordComposing) loadRecords(); });

    $('#select-all').addEventListener('change', event => selectCurrentPage(event.target.checked));
    $('#bulk-pass').addEventListener('click', () => bulkReview('确认通过', '通过'));
    $('#bulk-pending').addEventListener('click', () => bulkReview('待复核', '需人工复核'));

    $('#export-excel').addEventListener('click', () => {
      const params = new URLSearchParams(new FormData($('#filters')));

      window.location.href = `/api/export.xlsx?${params}`;
      if (recordsState.batchFilter) setBatchStep(4);
    });

    $('#records-page-prev').addEventListener('click', () => loadRecords({page: Math.max(1, recordsState.recordPage - 1), scrollToTable: true}));
    $('#records-page-next').addEventListener('click', () => loadRecords({page: recordsState.recordPage + 1, scrollToTable: true}));

    $('#clear-batch').addEventListener('click', () => {
      recordsState.batchFilter = '';
      $('#filters [name="task_id"]').value = '';
      $('#batch-scope').classList.add('hidden');
      loadRecords().catch(error => toast(error.message, 'danger'));
    });
    $$('[data-filter]').forEach(button => button.addEventListener('click', () => {
      $('#filters').elements.namedItem('overall').value = button.dataset.filter;
      loadRecords().catch(error => toast(error.message, 'danger'));
    }));
    $('#filters').addEventListener('reset', () => {
      clearTimeout(recordResetTimer);
      recordResetTimer = setTimeout(() => {
        recordResetTimer = null;
        $('#filters [name="task_id"]').value = recordsState.batchFilter;
        loadRecords();
      });
    });

    calendar.initialize();

    $('#bulk-delete').addEventListener('click', () => deleteRecords([...recordsState.selected]));

  }

  return {initialize, loadRecords, recordRow, toggleSelected, selectCurrentPage, updateSelectionToolbar, bulkReview, deleteRecords, setImportDate, openDayRecords, changeImportDate, scheduleRecordSearch, getReviewScope, startRecordsReview, removeFilter, renderFilterChips, setDensity, clearAdditionalFilters, setPageSize};
}
