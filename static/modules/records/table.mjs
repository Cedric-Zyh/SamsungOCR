/** Table rendering and row interactions; no network requests. */
import {escapeHtml} from '../core/ui.mjs';
import {activeFilterEntries} from './record_filters.mjs';

export function createRecordTable({environment, ui, recordsState, currentFilters, recordRow,
  toggleSelected, startRecordsReview, openProcessHistory, deleteRecords, retryOne}) {
  const {document, window, location} = environment;
  const {$, $$} = ui;
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

  function renderRecords({background, scrollToTable}) {
    const body = $('#records-body');
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
      $$('[data-open-review]', body).forEach(button => button.addEventListener('click', () => {
        const id = Number(button.dataset.openReview);
        if (button.dataset.reviewMode === 'view') {
          location.hash = `view/${id}`;
          return;
        }
        startRecordsReview('filtered', id);
      }));
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


  return {renderRecords, showRecordEmptyState};
}
