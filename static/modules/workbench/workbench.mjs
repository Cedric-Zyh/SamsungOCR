import {createWorkbenchSummary} from './summary.mjs';
import {createDailyQuery} from './query.mjs';
import {createApiClients} from '../core/api.mjs';
import {localToday} from '../core/ui.mjs';
import {createImportCalendar} from '../imports/import_calendar.mjs';
import {confirmInline} from '../core/inline_confirm.mjs';
import {createWorkFilterState} from './filters.mjs';
import {renderWorkbenchRow} from './row_renderer.mjs';

const WORK_PAGE_SIZE = 50;
const START_BATCH_SIZE = 5000;
import {WORK_FILTERS, matchesOverview, workFilterValues, selectionKey, deletionTarget} from './overview.mjs';

export function createProgress({
  environment, ui, importsState, progressState, recordsState, reportState, resultsState, reviewState,
  api, services = createApiClients(api), ReceiptWorkbench, ReceiptQueue, retryOne, startReviewScope, openProcessHistory = () => {}
}) {
  services = services && typeof services === 'object' ? services : createApiClients(api);
  const {document, window, location, localStorage, FormData} = environment;
  const {$, $$, toast} = ui;
  const selected = new Set();
  const viewState = {shownItems: []};
  let deleting = false;
  const {activeWorkFilters, setWorkFilters, setWorkFilter, hasOnlyWorkFilter} = createWorkFilterState(progressState);
  const calendar = createImportCalendar({environment, ui, api, services, prefix: 'progress-calendar', popup: '#progress-calendar',
    readDate: () => $('#progress-date').value,
    selectDate: day => { $('#progress-date').value = day; changeProcessingDay(); }});

  const {renderTask, renderOverview} = createWorkbenchSummary({environment, ui, progressState, ReceiptWorkbench});
  const {loadDailyResults} = createDailyQuery({
    environment, ui, progressState, importsState, recordsState, reportState, resultsState,
    services, calendar, selected, viewState, pageSize: WORK_PAGE_SIZE,
    ReceiptQueue, ReceiptWorkbench, syncSelection, syncStartReview, syncStartRecognition,
    renderQueueControl, renderTask, renderWorkbenchRows, hasOnlyWorkFilter, setWorkFilter
  });

  async function showQueuedRetry(task) {
    $('#progress-date').value = (task.created_at || '').slice(0, 10) || localToday();
    try { localStorage.setItem('receipt-progress-date', $('#progress-date').value); } catch (_) {}
    setWorkFilter('processing');
    progressState.workVisibleLimit = WORK_PAGE_SIZE;
    location.hash = 'progress';
    toast('已加入后台识别队列，可以关闭页面。', 'success');
    await loadDailyResults();
  }

  function selectable(item) { return !!deletionTarget(item) && !importsState.uploadingJobs?.has(item.job?.id); }
  function syncSelection() {
    const current = viewState.shownItems.filter(selectable), all = $('#workbench-select-all');
    if (all) {
      all.checked = !!current.length && current.every(item => selected.has(selectionKey(item)));
      all.indeterminate = !all.checked && current.some(item => selected.has(selectionKey(item)));
      all.disabled = !current.length || deleting || !progressState.dailyReady;
    }
    const button = $('#overview-delete');
    if (button) {
      button.disabled = !selected.size || deleting || !progressState.dailyReady;
      button.textContent = deleting ? '正在删除…' : selected.size ? `删除所选（${selected.size}）` : '删除所选';
    }
    $$('[data-work-select]').forEach(input => {
      input.checked = selected.has(input.dataset.workSelect);
      input.disabled = deleting || !progressState.dailyReady || !viewState.shownItems.some(item => selectionKey(item) === input.dataset.workSelect && selectable(item));
    });
    if (selected.size && $('#workbench-page-note')) $('#workbench-page-note').textContent += ` · 已选 ${selected.size} 张`;
  }

  async function deleteSelected() {
    if (deleting || !progressState.dailyReady || progressState.queueDay !== $('#progress-date').value) return;
    const items = (progressState.workItems || []).filter(item => selected.has(selectionKey(item)) && selectable(item));
    if (!items.length) return;
    if (!window.confirm(`删除所选 ${items.length} 张单据？待处理任务将停止；已识别回单的关联页、同日同名历史记录也会删除。此操作无法撤销。`)) return;
    deleting = true; syncSelection();
    let removed = 0, failure = '';
    try {
      const jobs = items.filter(item => deletionTarget(item).kind === 'job');
      const recordIds = new Set(items.filter(item => deletionTarget(item).kind === 'record').map(item => Number(item.id)));
      for (let offset = 0; offset < jobs.length; offset += 5000) {
        const group = jobs.slice(offset, offset + 5000);
        const ids = group.map(item => item.job.id);
        const result = await (services.queue.cancel(ids));
        const deleted = new Set(result.deleted_ids || []);
        for (const item of group) if (deleted.has(item.job.id)) {
          removed++; selected.delete(selectionKey(item));
          if (item.id) recordIds.add(Number(item.id));
        }
        if (result.kept_ids?.length) failure = '部分任务已完成，已保留，请刷新后重新选择。';
      }
      const ids = [...recordIds];
      for (let offset = 0; offset < ids.length; offset += 2000) {
        const chunk = ids.slice(offset, offset + 2000);
        const result = await (services.records.remove(chunk));
        const deleted = new Set(result.ids || []);
        for (const item of items) if (deleted.has(Number(item.id))) {
          if (selected.has(selectionKey(item))) removed++;
          selected.delete(selectionKey(item));
        }
      }
    } catch (error) { failure = error.message; }
    finally {
      progressState.progressRecordCache = null;
      recordsState.recordsStale = reportState.reportStale = true;
      resultsState.resultRevision = (resultsState.resultRevision || 0) + 1;
      await loadDailyResults().catch(error => { failure ||= error.message; });
      deleting = false; syncSelection();
      toast(failure ? `已删除 ${removed} 张；${failure}` : `已删除 ${removed} 张单据。`, failure ? 'warning' : 'success');
    }
  }

  function renderWorkbenchRows() {
    if (progressState.workFilters !== null && progressState.workFilters !== undefined) {
      progressState.workFilters = workFilterValues(progressState.workFilters);
    } else if (!Object.hasOwn(WORK_FILTERS, progressState.workFilter)) {
      setWorkFilter('review');
    }
    const filters = activeWorkFilters();
    const items = progressState.workItems || [], visible = ReceiptWorkbench.ordered(items).filter(item => matchesOverview(item, filters, ReceiptWorkbench, progressState.workType || '', progressState.workSearch || ''));
    const limit = progressState.workVisibleLimit || WORK_PAGE_SIZE, shown = visible.slice(0, limit);
    $$('[data-work-filter]').forEach(button => {
      const value = button.dataset.workFilter;
      const buttonFilters = workFilterValues(value);
      const pressed = value === 'all' ? !filters.length
        : buttonFilters.length ? buttonFilters.every(item => filters.includes(item)) : filters.includes(value);
      button.setAttribute('aria-pressed', String(pressed));
    });
    $$('[data-work-count]').forEach(label => label.textContent = items.filter(item => matchesOverview(item, label.dataset.workCount, ReceiptWorkbench)).length);
    viewState.shownItems = shown;
    const existing = new Set(items.filter(selectable).map(selectionKey));
    for (const key of selected) if (!existing.has(key)) selected.delete(key);
    renderOverview(items);
    const label = filters.length ? (filters.length === 2 && filters.includes('ready') && filters.includes('running')
      ? WORK_FILTERS.processing : filters.map(value => WORK_FILTERS[value]).join('、')) : WORK_FILTERS.all;
    $('#workbench-visible-count').textContent = `${label} ${visible.length} 张`;
    if ($('#workbench-total-label')) $('#workbench-total-label').textContent = `当天共 ${items.length} 张`;
    if ($('#workbench-list-title')) $('#workbench-list-title').textContent = `${label}单据`;
    if ($('#workbench-reason-heading')) $('#workbench-reason-heading').textContent = filters.length && filters.every(value => ['ready', 'running'].includes(value)) ? '处理进度' : '需要关注 / 核验结果';
    if ($('#workbench-page-note')) $('#workbench-page-note').textContent = `已显示 ${shown.length} / ${visible.length} 张`;
    if ($('#workbench-more')) {
      $('#workbench-more').classList.toggle('hidden', shown.length >= visible.length);
      $('#workbench-more').textContent = `再显示 ${Math.min(WORK_PAGE_SIZE, visible.length - shown.length)} 张`;
    }
    syncStartReview();
    syncStartRecognition();
    const markup = shown.map(item => renderWorkbenchRow(item, {
      ReceiptWorkbench, importsState, progressState, selected, deleting, selectable
    })).join('') || `<tr><td colspan="5" class="empty-state"><strong>${!items.length ? '这一天还没有回单' : `暂无符合条件的${label}单据`}</strong><p>${!items.length ? '选择其他日期，或点击上方“导入回单”。' : hasOnlyWorkFilter('review') && items.some(item => ReceiptWorkbench.category(item) === 'processing') ? '识别完成后，需要确认的回单会出现在这里。' : '可切换其他状态，或查看当天全部记录。'}</p></td></tr>`;
    if (progressState.queueMarkup !== markup) { $('#queue').innerHTML = markup; progressState.queueMarkup = markup; }
    syncSelection();
  }

  function reviewItems() {
    return ReceiptWorkbench.ordered(progressState.workItems || []).filter(item => item.id && ReceiptWorkbench.category(item) === 'review');
  }

  function syncStartReview() {
    const button = $('#workbench-start-review');
    const queueToggle = $('#queue-toggle');
    const cancelAll = $('#workbench-cancel-all');
    const processingOnly = hasOnlyWorkFilter('processing');
    const reviewOnly = hasOnlyWorkFilter('review');
    queueToggle?.classList.remove('hidden');
    cancelAll?.classList.toggle('hidden', !processingOnly);
    if (cancelAll) {
      const jobs = cancelableJobs();
      cancelAll.disabled = !jobs.length || !!progressState.queueCancelSaving || !!progressState.queueStartSaving || !!progressState.queueControlSaving;
      cancelAll.textContent = progressState.queueCancelSaving ? '正在取消…' : `全部取消（${jobs.length}）`;
      cancelAll.title = jobs.length ? `删除当天 ${jobs.length} 张待开始或处理中的回单` : '当天没有可取消的待处理回单';
      cancelAll.setAttribute('aria-busy', String(!!progressState.queueCancelSaving));
    }
    if (!button) return;
    button.classList.toggle('hidden', !reviewOnly && !processingOnly);
    const ready = progressState.dailyReady && progressState.queueDay === $('#progress-date').value;
    if (processingOnly) {
      button.textContent = '开始识别';
      button.disabled = !ready || !readyJobs().length || !!progressState.queueStartSaving;
      return;
    }
    button.textContent = '开始复核';
    button.disabled = !ready || !reviewItems().length || !!progressState.workReviewOpening;
  }

  function readyJobs() {
    const day = $('#progress-date').value;
    if (!progressState.dailyReady || progressState.queueDay !== day) return [];
    return [...(progressState.queueJobs?.values() || [])].filter(job => job.status === 'ready'
      && job.start_requested !== true && job.id
      && (job.import_date || job.created_at?.slice(0, 10) || progressState.queueDay) === day);
  }

  function cancelableJobs() {
    const day = $('#progress-date').value;
    if (!progressState.dailyReady || progressState.queueDay !== day) return [];
    return [...(progressState.queueJobs?.values() || [])].filter(job =>
      ['awaiting_upload', 'ready', 'queued', 'running'].includes(job.status)
      && job.id
      && (job.import_date || job.created_at?.slice(0, 10) || progressState.queueDay) === day);
  }

  async function cancelAllJobs() {
    const jobs = cancelableJobs();
    if (!jobs.length || progressState.queueCancelSaving) return;
    if (!window.confirm(`将删除当天 ${jobs.length} 张待开始或处理中回单，删除后不会再识别。确定继续吗？`)) return;
    const ids = jobs.map(job => job.id);
    progressState.queueCancelSaving = true;
    progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    syncStartReview(); renderQueueControl();
    try {
      const result = await (services.queue.cancel(ids));
      const count = Number(result.deleted_ids?.length) || 0;
      toast(`已取消并删除 ${count} 张待处理回单。`, 'success');
      setWorkFilter('processing');
      progressState.workVisibleLimit = WORK_PAGE_SIZE;
      await loadDailyResults({refreshRecords: false});
    } catch (error) {
      toast(error.message, 'danger');
    } finally {
      progressState.queueCancelSaving = false;
      syncStartReview(); renderQueueControl();
    }
  }

  function syncStartRecognition() {
    const button = $('#queue-start');
    if (!button) return;
    const jobs = readyJobs(), uploading = importsState.batchRunning || importsState.importScanning || importsState.uploadingJobs?.size;
    button.textContent = progressState.queueStartSaving ? '正在开始…' : `开始识别（${jobs.length}）`;
    button.disabled = !jobs.length || !!uploading || !!progressState.queueStartSaving || !!progressState.queueControlSaving;
    button.setAttribute('aria-busy', String(!!progressState.queueStartSaving));
    button.title = uploading ? '请等待当前图片读取和上传完成' : jobs.length ? `开始识别 ${$('#progress-date').value} 的 ${jobs.length} 张待开始回单` : '当天暂无已上传且待开始的回单';
    const note = $('#queue-start-note');
    if (note) note.textContent = uploading ? '正在读取或上传图片，完成后可开始识别。'
      : jobs.length ? `当天 ${jobs.length} 张回单已上传，等待你点击开始识别。` : '导入的回单先保存在本地，上传完成后点击开始识别。';
    $('#queue-start-remote-note')?.classList.toggle('hidden', !jobs.some(job => job.uses_remote));
  }

  async function startRecognition() {
    syncStartRecognition();
    if ($('#queue-start')?.disabled) return;
    const day = $('#progress-date').value, ids = readyJobs().map(job => job.id);
    let started = 0, released = 0;
    progressState.queueStartSaving = true;
    progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    syncStartRecognition(); syncStartReview(); renderQueueControl();
    try {
      for (let offset = 0; offset < ids.length; offset += START_BATCH_SIZE) {
        const group = ids.slice(offset, offset + START_BATCH_SIZE);
      const result = await (services.queue.start(group));
        started += Number(result.started) || 0; released += group.length;
        // A day change during the request must keep its own list and controls.
        if (progressState.queueDay === day && $('#progress-date').value === day) {
          for (const job of result.items || []) if (progressState.queueJobs.has(job.id)) progressState.queueJobs.set(job.id, job);
          setWorkFilter('processing'); progressState.workVisibleLimit = WORK_PAGE_SIZE;
        }
        if (result.control) progressState.queueControl = result.control;
      }
      toast(progressState.queueControl?.paused ? `已安排 ${started} 张回单；后台队列已暂停，点击“继续识别”后处理。`
        : `已开始 ${started} 张回单的识别。`, 'success');
    } catch (error) {
      toast(released ? `已提交 ${released} 张回单，剩余 ${ids.length - released} 张的开始状态待确认：${error.message}` : error.message, 'danger');
    } finally {
      await loadDailyResults({refreshRecords: false}).catch(error => toast(error.message, 'danger'));
      progressState.queueStartSaving = false; syncStartRecognition(); syncStartReview(); renderQueueControl();
    }
  }

  function renderQueueControl() {
    const control = progressState.queueControl;
    if (!control) return;
    const pausing = control.status === 'pausing';
    $('#queue-control-label').textContent = `所有日期 · ${control.paused ? (pausing ? '暂停中' : '已暂停') : '识别开启'}`;
    $('#queue-control-label').classList.toggle('paused', control.paused);
    $('#queue-toggle').textContent = control.paused ? '继续' : '暂停';
    $('#queue-toggle').disabled = !!progressState.queueControlSaving || !!progressState.queueStartSaving;
    $('#queue-toggle').classList.toggle('resume-queue', control.paused);
    $('#queue-toggle').title = control.paused ? '继续查询及所有日期中已点击开始的回单；待开始回单仍保持等待' : '暂停单证通后续查询及后续回单；已发出的请求结束后暂停';
    const notice = $('#queue-pause-notice');
    notice.classList.toggle('hidden', !control.paused);
    notice.textContent = pausing ? `所有日期：已请求暂停查询，已发出的请求结束后不再续查；${control.queued} 张等待继续。`
      : `所有日期共 ${control.queued} 张等待继续。暂停期间可照常复核和导入。`;
    syncStartRecognition();
    syncStartReview();
  }

  function changeProcessingDay() {
    try { localStorage.setItem('receipt-progress-date', $('#progress-date').value); } catch (_) {}
    loadDailyResults().catch(error => toast(error.message,'danger'));
  }

  function shiftProcessingDay(offset) {
    const day = new Date(`${$('#progress-date').value || localToday()}T12:00:00`);
    day.setDate(day.getDate() + offset);
    $('#progress-date').value = `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;
    changeProcessingDay();
  }

  function initialize() {
    $('#overview-delete')?.addEventListener('click', deleteSelected);
    $('#workbench-select-all')?.addEventListener('change', event => {
      if (deleting || !progressState.dailyReady) return;
      viewState.shownItems.filter(selectable).forEach(item => event.target.checked ? selected.add(selectionKey(item)) : selected.delete(selectionKey(item)));
      renderWorkbenchRows();
    });
    $('#queue').addEventListener('change', event => {
      const key = event.target.dataset.workSelect;
      if (!key || deleting || !progressState.dailyReady || !viewState.shownItems.some(item => selectionKey(item) === key && selectable(item))) return;
      event.target.checked ? selected.add(key) : selected.delete(key);
      renderWorkbenchRows();
    });
    $('#workbench-search')?.addEventListener('input', event => {
      progressState.workSearch = event.target.value; selected.clear();
      progressState.workVisibleLimit = WORK_PAGE_SIZE; renderWorkbenchRows();
    });
    $('#workbench-type-counts')?.addEventListener('click', event => {
      const button = event.target.closest('[data-work-type]');
      if (!button) return;
      progressState.workType = button.dataset.workType; selected.clear();
      progressState.workVisibleLimit = WORK_PAGE_SIZE; renderWorkbenchRows();
    });
    $('#queue-start')?.addEventListener('click', startRecognition);
    $$('[data-work-filter]').forEach(button => button.addEventListener('click', () => {
      selected.clear();
      const filter = button.dataset.workFilter;
      if (filter === 'all') {
        setWorkFilters([]);
        progressState.workType = ''; progressState.workSearch = '';
        if ($('#workbench-search')) $('#workbench-search').value = '';
      } else {
        // The initial scalar value is a default view, not an explicit
        // multi-select choice. The first card click should select that card
        // alone; subsequent clicks toggle additional cards.
        const filters = new Set(progressState.workFilters === null ? [] : activeWorkFilters());
        if (filters.has(filter)) filters.delete(filter); else filters.add(filter);
        setWorkFilters([...filters]);
      }
      progressState.workFilterChosen = true;
      progressState.workVisibleLimit = WORK_PAGE_SIZE;
      renderWorkbenchRows();
      $('.workbench-table-wrap')?.scrollTo({top:0});
    }));

    $('#workbench-more')?.addEventListener('click', () => {
      progressState.workVisibleLimit = (progressState.workVisibleLimit || WORK_PAGE_SIZE) + WORK_PAGE_SIZE;
      renderWorkbenchRows();
    });

    $('#workbench-start-review')?.addEventListener('click', async () => {
      syncStartReview();
      if ($('#workbench-start-review').disabled) return;
      if (hasOnlyWorkFilter('processing')) { await startRecognition(); return; }
      const day = $('#progress-date').value, first = reviewItems()[0];
      progressState.workReviewOpening = true;
      syncStartReview();
      try { await startReviewScope({kind: 'day', label: `${day} · 当天全部`, filters: {import_date: day}}, {recordId: Number(first.id)}); }
      catch (error) { toast(error.message, 'danger'); }
      finally { progressState.workReviewOpening = false; syncStartReview(); }
    });
    $('#workbench-cancel-all')?.addEventListener('click', cancelAllJobs);

    $('#queue-toggle').addEventListener('click', async () => {
      if (!progressState.queueControl || progressState.queueControlSaving || progressState.queueStartSaving) return;
      progressState.queueControlSaving = true;
      progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
      renderQueueControl();
      try {
        progressState.queueControl = await (services.queue.control({method:'POST', json:{paused:!progressState.queueControl.paused}}));
        renderQueueControl();
        await loadDailyResults({refreshRecords:false});
      } catch (error) { toast(error.message, 'danger'); }
      finally { progressState.queueControlSaving = false; renderQueueControl(); }
    });

    $('#queue').addEventListener('click', async event => {
      const view = event.target.closest('[data-work-view]');
      if (view) { location.hash = `view/${Number(view.dataset.workView)}`; return; }
      const process = event.target.closest('[data-process-job], [data-process-result]');
      if (process) {
        openProcessHistory({
          jobId: process.dataset.processJob || '',
          resultId: process.dataset.processResult || '',
          filename: process.dataset.filename || ''
        });
        return;
      }
      const cancel = event.target.closest('[data-job-cancel]');
      if (cancel) {
        const id = cancel.dataset.jobCancel, job = progressState.queueJobs?.get(id);
        if (!job || cancel.disabled || importsState.uploadingJobs?.has(id) || progressState.workPendingJobs?.has(id)) return;
        if (!window.confirm(`取消后将删除“${job.filename || '这条回单'}”，且不会再识别。确定继续吗？`)) return;
        progressState.workPendingJobs ||= new Set();
        progressState.workPendingJobs.add(id);
        cancel.disabled = true;
        try {
          await (services.queue.cancelOne(id));
          toast('已取消并删除这条待处理回单。', 'success');
          await loadDailyResults({refreshRecords: false});
        } catch (error) {
          toast(error.message, 'danger');
          cancel.disabled = false;
        } finally {
          progressState.workPendingJobs.delete(id);
        }
        return;
      }
      const start = event.target.closest('[data-job-start]');
      if (start) {
        const id = start.dataset.jobStart, job = progressState.queueJobs?.get(id);
        if (!job || start.disabled || progressState.workPendingJobs?.has(id)) return;
        progressState.workPendingJobs ||= new Set(); progressState.workPendingJobs.add(id); start.disabled = true;
        try {
          await (services.queue.start([id]));
          setWorkFilter('processing'); progressState.workVisibleLimit = WORK_PAGE_SIZE;
          await loadDailyResults({refreshRecords: false});
        } catch (error) { toast(error.message, 'danger'); start.disabled = false; }
        finally { progressState.workPendingJobs.delete(id); }
        return;
      }
      const retry = event.target.closest('[data-work-retry]');
      if (retry) { await retryOne(Number(retry.dataset.workRetry), retry); return; }
      const review = event.target.closest('[data-work-review]');
      if (review) {
        const day = $('#progress-date').value;
        await startReviewScope({kind: 'day', label: `${day} · 当天全部`, filters: {import_date: day}}, {recordId: Number(review.dataset.workReview)})
          .catch(error => toast(error.message,'danger'));
        return;
      }
      const button = event.target.closest('[data-job-retry], [data-job-upload]');
      if (!button) return;
      const id = button.dataset.jobRetry || button.dataset.jobUpload;
      const job = progressState.queueJobs?.get(id);
      if (!job || button.disabled || importsState.uploadingJobs?.has(id) || progressState.workPendingJobs?.has(id)) return;
      // Reserve the job before the asynchronous confirmation opens. Another
      // click must not reach the same request while this dialog is pending.
      progressState.workPendingJobs ||= new Set();
      progressState.workPendingJobs.add(id);
      button.disabled = true;
      if (job.uses_remote && !(button.dataset.jobUpload && job.start_requested === false)) {
        let confirmed = false;
        try {
          confirmed = await confirmInline({environment, anchor: button, message: '此任务使用已保存的远程识别配置，会将完整回单上传至所选服务。'});
        } finally {
          if (!confirmed) { progressState.workPendingJobs.delete(id); button.disabled = false; }
        }
        if (!confirmed) return;
      }
      if (button.dataset.jobRetry) {
        progressState.workPendingJobs ||= new Set(); progressState.workPendingJobs.add(id);
        button.disabled = true;
        try {
          await (services.queue.retry(id));
          setWorkFilter('processing'); progressState.workVisibleLimit = WORK_PAGE_SIZE;
          await loadDailyResults();
        }
        catch (error) { toast(error.message, 'danger'); button.disabled = false; }
        finally { progressState.workPendingJobs.delete(id); }
        return;
      }
      const input = document.createElement('input'); input.type = 'file'; input.accept = 'image/jpeg,image/png,image/bmp,image/webp';
      progressState.workPendingJobs ||= new Set(); progressState.workPendingJobs.add(id);
      button.disabled = true;
      const release = () => { progressState.workPendingJobs.delete(id); button.disabled = false; };
      input.addEventListener('cancel', release);
      input.addEventListener('change', async () => {
        if (!input.files.length) { release(); return; }
        importsState.uploadingJobs ||= new Set(); importsState.uploadingJobs.add(id); button.disabled = true;
        renderWorkbenchRows();
        $('#progress-note').textContent = '正在上传，请保持页面打开。';
        const form = new FormData(); form.append('file', input.files[0]);
        try { await (services.queue.upload(id, form)); }
        catch (error) { toast(error.message, 'danger'); }
        finally {
          importsState.uploadingJobs.delete(id); release();
          await loadDailyResults().catch(error => toast(error.message, 'danger'));
        }
      });
      input.click();
    });

    $('#progress-date').value = localToday();
    try { const saved = localStorage.getItem('receipt-progress-date'); if (/^\d{4}-\d{2}-\d{2}$/.test(saved || '')) $('#progress-date').value = saved; } catch (_) {}
    // Cached templates can keep using their native picker until refreshed.
    if ($('#progress-date').type === 'hidden') calendar.initialize();

    $('#progress-date').addEventListener('change', changeProcessingDay);
    $('#refresh-progress').addEventListener('click', changeProcessingDay);

    $('#progress-prev').addEventListener('click', () => shiftProcessingDay(-1));
    $('#progress-next').addEventListener('click', () => shiftProcessingDay(1));
    $('#progress-today').addEventListener('click', () => { $('#progress-date').value = localToday(); changeProcessingDay(); });
    syncStartRecognition();

  }

  return {initialize, showQueuedRetry, renderTask, renderWorkbenchRows, renderQueueControl, syncStartRecognition, startRecognition, cancelAllJobs, loadDailyResults, changeProcessingDay, shiftProcessingDay};
}
