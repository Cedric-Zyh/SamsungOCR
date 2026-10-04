import {createRecordTable} from './table.mjs';
import {createRecordQuery} from './query.mjs';
import {createApiClients} from '../core/api.mjs';
import {localToday, escapeHtml} from '../core/ui.mjs';
import {activeFilterEntries, recordReviewScope} from './record_filters.mjs';
import {createImportCalendar} from '../imports/import_calendar.mjs';
import {createRecordRow} from './record_row.mjs';
import {createRecordSelection} from './selection.mjs';

const FIXED_RECORD_PAGE_SIZE = 25;

export function createRecords({
  environment, ui, ReceiptWorkbench, recordsState, api, services = createApiClients(api), startReviewScope, retryOne,
  refreshVisibleResults, setBatchStep, openProcessHistory = () => {}
}) {
  services = services && typeof services === 'object' ? services : createApiClients(api);
  const {document, window, location, FormData, URLSearchParams, AbortController, setTimeout, clearTimeout} = environment;
  const {$, $$, toast} = ui;
  const recordRow = createRecordRow({ReceiptWorkbench, recordsState});
  let recordResetTimer;
  const searchState = {timer: null, composing: false};
  let tablePopover, tablePopoverAnchor;
  let bulkActionPending = false;
  const selection = createRecordSelection({
    recordsState, $, $$, canBulkConfirm: item => canBulkConfirm(item),
    isBusy: () => !!recordsState.recordLoading || bulkActionPending || searchState.composing
  });
  const {toggleSelected, selectCurrentPage, updateSelectionToolbar} = selection;
  const calendar = createImportCalendar({environment, ui, api, services, prefix: 'calendar', popup: '#import-calendar',
    readDate: () => $('#import-date').value,
    selectDate: day => { setImportDate(day); changeImportDate(); }});

  const {renderRecords, showRecordEmptyState} = createRecordTable({
    environment, ui, recordsState, currentFilters, recordRow, toggleSelected,
    startRecordsReview, openProcessHistory, deleteRecords, retryOne
  });
  const {loadRecords, scheduleRecordSearch} = createRecordQuery({
    environment, ui, recordsState, services, searchState, syncColumnFilters, renderFilterChips,
    closeMoreMenu: () => { if (tablePopover?.id === 'record-more-menu') closeTablePopover(); },
    updateSelectionToolbar, showRecordEmptyState, renderRecords
  });

  function currentFilters() {
    const filters = Object.fromEntries(new FormData($('#filters')));
    filters.task_id = recordsState.batchFilter || '';
    return filters;
  }
  function getReviewScope(kind = 'filtered') {
    return recordReviewScope(currentFilters(), kind, [...recordsState.selected]);
  }
  async function startRecordsReview(kind = 'filtered', recordId) {
    if (searchState.composing || recordsState.recordLoading || searchState.timer) {
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
    const control = $('#records-page-size');
    if (control) control.value = String(size);
    try { environment.localStorage?.setItem('receipt.records.page-size', String(size)); } catch (_) { /* The visible UI keeps the fixed page size. */ }
    await loadRecords({page: 1, scrollToTable: true});
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

    clearTimeout(searchState.timer);
    clearTimeout(recordResetTimer);
    searchState.timer = recordResetTimer = null;
    searchState.composing = false;
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

  function isRecordFilter(target) { return target.form === $('#filters') && !['hidden','reset','submit'].includes(target.type); }

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
      await (services.records.bulkReview({ids, review_status: reviewStatus, final_result: finalResult}));
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
      await (services.records.remove(ids));
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
    const pageSizeControl = $('#records-page-size');
    if (pageSizeControl) {
      try {
        const savedSize = Number(environment.localStorage?.getItem('receipt.records.page-size'));
        if ([25, 50, 100].includes(savedSize)) recordsState.recordPageSize = savedSize;
      } catch (_) { /* Keep the default page size. */ }
      pageSizeControl.value = String(recordsState.recordPageSize);
      pageSizeControl.addEventListener('change', event => setPageSize(event.target.value));
    } else {
      recordsState.recordPageSize = FIXED_RECORD_PAGE_SIZE;
    }
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
      $('#record-more-menu').dataset.filename = button.dataset.moreFilename || '';
      showTablePopover($('#record-more-menu'), button);
    });
    $('[data-menu-process]').addEventListener('click', () => {
      const menu = $('#record-more-menu'), id = Number(menu.dataset.recordId);
      const item = recordsState.records.find(record => Number(record.id) === id);
      closeTablePopover();
      openProcessHistory({resultId: id, filename: menu.dataset.filename || item?.filename || ''});
    });
    $('[data-menu-retry]').addEventListener('click', () => {
      const id = Number($('#record-more-menu').dataset.recordId);
      closeTablePopover(); retryOne(id);
    });
    $('[data-menu-delete]').addEventListener('click', () => {
      const id = Number($('#record-more-menu').dataset.recordId);
      closeTablePopover(); deleteRecords([id]);
    });

    document.addEventListener('compositionstart', event => { if (isRecordFilter(event.target)) { searchState.composing = true; scheduleRecordSearch(); } });
    document.addEventListener('compositionend', event => { if (isRecordFilter(event.target)) { searchState.composing = false; scheduleRecordSearch(); } });
    document.addEventListener('input', event => { if (isRecordFilter(event.target)) scheduleRecordSearch(event.target.type === 'date' || event.target.tagName === 'SELECT'); });
    document.addEventListener('change', event => { if (isRecordFilter(event.target) && ['SELECT', 'INPUT'].includes(event.target.tagName) && event.target.type !== 'search') scheduleRecordSearch(true); });
    $('#filters').addEventListener('submit', event => { event.preventDefault(); if (!searchState.composing) loadRecords(); });

    $('#select-all').addEventListener('change', event => selectCurrentPage(event.target.checked));
    $('#bulk-pass').addEventListener('click', () => bulkReview('确认通过', '通过'));
    $('#bulk-pending').addEventListener('click', () => bulkReview('待复核', '需人工复核'));

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
