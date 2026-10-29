/** Selection state and toolbar rendering for the records table. */
export function recordSelectionKey(record) {
  if (record.processing_status) {
    return ['awaiting_upload', 'ready', 'queued', 'failed'].includes(record.processing_status)
      && record.processing_job?.id ? `job:${record.processing_job.id}` : null;
  }
  return record.id ? Number(record.id) : null;
}

export function createRecordSelection({recordsState, $, $$, canBulkConfirm, isBusy}) {
  function toggleSelected(id, checked) {
    if (!recordsState.records.some(record => recordSelectionKey(record) === id)) return;
    checked ? recordsState.selected.add(id) : recordsState.selected.delete(id);
    updateSelectionToolbar();
  }

  function selectCurrentPage(checked) {
    recordsState.records.map(recordSelectionKey).filter(key => key !== null).forEach(key => checked
      ? recordsState.selected.add(key)
      : recordsState.selected.delete(key));
    $$('.record-select').forEach(input => { if (!input.disabled) input.checked = checked; });
    updateSelectionToolbar();
  }

  function updateSelectionToolbar() {
    const selected = recordsState.records.filter(item => recordsState.selected.has(recordSelectionKey(item)));
    const pendingCount = selected.filter(item => item.processing_status).length;
    const unsafeCount = selected.filter(item => !item.processing_status && !canBulkConfirm(item)).length;
    const busy = isBusy();
    $('#selection-toolbar').classList.toggle('hidden', !recordsState.selected.size);
    $('#selection-toolbar').setAttribute('aria-busy', String(busy));
    $('#selection-count').textContent = `本页已选 ${recordsState.selected.size} 张`;
    $('#selection-readiness').setAttribute('data-tone', unsafeCount || pendingCount ? 'warning' : '');
    $('#selection-readiness').textContent = busy ? '正在处理选中回单…'
      : pendingCount ? `${pendingCount} 张尚未完成识别，可删除，识别完成后可复核`
      : unsafeCount ? `${unsafeCount} 张需逐张确认日期或印章`
      : selected.length ? '选中回单均可批量通过' : '';
    $('#bulk-pass').disabled = busy || !selected.length || pendingCount > 0 || unsafeCount > 0;
    $('#bulk-pass').title = pendingCount ? '所选回单尚未全部完成识别'
      : unsafeCount ? `${unsafeCount} 张日期或印章证据不完整，请先逐张复核` : '批量通过当前页选中的回单';
    for (const id of ['bulk-delete', 'clear-record-selection']) $(`#${id}`).disabled = busy || !selected.length;
    $('#bulk-pending').disabled = busy || !selected.length || pendingCount > 0;
    $('#bulk-retry').disabled = busy || !selected.length || pendingCount > 0;
    $('#review-selected-records').textContent = `复核选中（${recordsState.selected.size}）`;
    $('#review-selected-records').disabled = !recordsState.selected.size || busy || pendingCount > 0;
    $('#review-filtered-records').disabled = busy || !recordsState.recordsLoaded || !recordsState.recordTotal;
    const eligible = recordsState.records.map(recordSelectionKey).filter(key => key !== null);
    $('#select-all').disabled = busy || !eligible.length;
    $('#select-all').checked = !!eligible.length && eligible.every(key => recordsState.selected.has(key));
    $('#select-all').indeterminate = recordsState.selected.size > 0 && !$('#select-all').checked;
  }

  return {toggleSelected, selectCurrentPage, updateSelectionToolbar};
}
