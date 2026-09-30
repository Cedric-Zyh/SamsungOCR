/** Selection state and toolbar rendering for the records table. */
export function createRecordSelection({recordsState, $, $$, canBulkConfirm, isBusy}) {
  function toggleSelected(id, checked) {
    checked ? recordsState.selected.add(id) : recordsState.selected.delete(id);
    updateSelectionToolbar();
  }

  function selectCurrentPage(checked) {
    recordsState.records.forEach(record => checked
      ? recordsState.selected.add(Number(record.id))
      : recordsState.selected.delete(Number(record.id)));
    $$('.record-select').forEach(input => { input.checked = checked; });
    updateSelectionToolbar();
  }

  function updateSelectionToolbar() {
    const selected = recordsState.records.filter(item => recordsState.selected.has(Number(item.id)));
    const unsafeCount = selected.filter(item => !canBulkConfirm(item)).length;
    const busy = isBusy();
    $('#selection-toolbar').classList.toggle('hidden', !recordsState.selected.size);
    $('#selection-toolbar').setAttribute('aria-busy', String(busy));
    $('#selection-count').textContent = `本页已选 ${recordsState.selected.size} 张`;
    $('#selection-readiness').setAttribute('data-tone', unsafeCount ? 'warning' : '');
    $('#selection-readiness').textContent = busy ? '正在处理选中回单…'
      : unsafeCount ? `${unsafeCount} 张需逐张确认日期或印章`
      : selected.length ? '选中回单均可批量通过' : '';
    $('#bulk-pass').disabled = busy || !selected.length || unsafeCount > 0;
    $('#bulk-pass').title = unsafeCount ? `${unsafeCount} 张日期或印章证据不完整，请先逐张复核` : '批量通过当前页选中的回单';
    for (const id of ['bulk-pending', 'bulk-delete', 'clear-record-selection']) $(`#${id}`).disabled = busy || !selected.length;
    $('#review-selected-records').textContent = `复核选中（${recordsState.selected.size}）`;
    $('#review-selected-records').disabled = !recordsState.selected.size || busy;
    $('#review-filtered-records').disabled = !!recordsState.recordLoading;
    $('#select-all').checked = !!recordsState.records.length && recordsState.records.every(record => recordsState.selected.has(Number(record.id)));
    $('#select-all').indeterminate = recordsState.selected.size > 0 && !$('#select-all').checked;
  }

  return {toggleSelected, selectCurrentPage, updateSelectionToolbar};
}
