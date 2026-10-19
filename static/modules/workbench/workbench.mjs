import {createWorkbenchSummary} from './summary.mjs';
import {createDailyQuery} from './query.mjs';
import {createApiClients} from '../core/api.mjs';
import {localToday} from '../core/ui.mjs';
import {createImportCalendar} from '../imports/import_calendar.mjs';
import {confirmInline} from '../core/inline_confirm.mjs';
import {createWorkFilterState} from './filters.mjs';
import {renderTaskCard} from './task_cards.mjs';
import {createTaskDrawer} from './task_drawer.mjs';

const WORK_PAGE_SIZE = 50;
const START_BATCH_SIZE = 5000;
import {WORK_FILTERS, matchesOverview, workFilterValues, selectionKey, overviewStatus, orderedQueue} from './overview.mjs';

export function createProgress({
  environment, ui, importsState, progressState, recordsState, reportState, resultsState,
  api, services = createApiClients(api), ReceiptWorkbench, ReceiptQueue, retryOne, startReviewScope,
  openProcessHistory = () => {}, openResultPreview = () => {}, flushSettings = async () => {},
  onQueueChanged = () => {}, openManagedRecords = () => {}
}) {
  services = services && typeof services === 'object' ? services : createApiClients(api);
  const {document, window, location, localStorage, FormData} = environment;
  const {$, $$, toast} = ui;
  const selected = new Set();
  const viewState = {shownItems: []};
  let queueActionSaving = false;
  let reviewOpening = false;
  const {activeWorkFilters, setWorkFilters, setWorkFilter} = createWorkFilterState(progressState);
  const calendar = createImportCalendar({environment, ui, api, services, prefix: 'progress-calendar', popup: '#progress-calendar',
    readDate: () => $('#progress-date').value,
    selectDate: day => { $('#progress-date').value = day; changeProcessingDay(); }});

  const {renderTask, renderOverview} = createWorkbenchSummary({environment, ui, progressState, ReceiptWorkbench});
  const {loadDailyResults} = createDailyQuery({
    environment, ui, progressState, importsState, recordsState, reportState, resultsState,
    services, calendar, selected, viewState, pageSize: WORK_PAGE_SIZE,
    ReceiptQueue, ReceiptWorkbench, syncSelection, syncStartRecognition,
    renderQueueControl, renderTask, renderWorkbenchRows, onQueueChanged
  });
  const drawer = createTaskDrawer({environment, ui, progressState, renderWorkbenchRows, openManagedRecords});

  async function showQueuedRetry(task) {
    $('#progress-date').value = (task.created_at || '').slice(0, 10) || localToday();
    try { localStorage.setItem('receipt-progress-date', $('#progress-date').value); } catch (_) {}
    setWorkFilter('processing');
    progressState.workVisibleLimit = WORK_PAGE_SIZE;
    openManagedRecords($('#progress-date').value);
    drawer.open();
    toast('已加入后台识别队列，可以关闭页面。', 'success');
    await loadDailyResults();
  }

  function reviewable(item) { return Number.isSafeInteger(item.id) && item.id > 0 && overviewStatus(item, ReceiptWorkbench) === 'completed'; }
  function selectable(item) {
    if (importsState.uploadingJobs?.has(item.job?.id) || progressState.workPendingJobs?.has(item.job?.id)) return false;
    return !['running', 'cancelled'].includes(item.status) && !!(item.id || item.job?.id);
  }
  function syncSelection() {
    const current = viewState.shownItems.filter(selectable), all = $('#workbench-select-all');
    if (all) {
      all.checked = !!current.length && current.every(item => selected.has(selectionKey(item)));
      all.indeterminate = !all.checked && current.some(item => selected.has(selectionKey(item)));
      all.disabled = !current.length || queueActionSaving || !progressState.dailyReady;
    }
    const chosen = current.filter(item => selected.has(selectionKey(item)));
    $('#queue-selection')?.classList.toggle('hidden', !chosen.length);
    if ($('#queue-selection-count')) $('#queue-selection-count').textContent = `已选 ${chosen.length} 张`;
    const deleteButton = $('#queue-selected-delete');
    if (deleteButton) {
      deleteButton.textContent = `删除所选（${chosen.length}）`;
      deleteButton.disabled = !chosen.length || queueActionSaving || !progressState.dailyReady || !!progressState.queueStartSaving;
    }
    for (const [selector, status, label] of [['#queue-selected-start', 'ready', '开始所选'], ['#queue-selected-withdraw', 'queued', '取消所选排队']]) {
      const button = $(selector), count = chosen.filter(item => item.status === status).length;
      if (button) { button.textContent = `${label}（${count}）`; button.classList.toggle('hidden', count === 0); button.disabled = !count || queueActionSaving || !progressState.dailyReady || !!progressState.queueStartSaving; }
    }
    const reviewButton = $('#queue-selected-review'), reviewCount = chosen.filter(reviewable).length;
    if (reviewButton) {
      reviewButton.textContent = reviewOpening ? '正在打开…' : `复核所选（${reviewCount}）`;
      reviewButton.classList.toggle('hidden', !reviewCount);
      reviewButton.disabled = !reviewCount || reviewOpening || queueActionSaving || !progressState.dailyReady;
    }
    $$('[data-work-select]').forEach(input => {
      input.checked = selected.has(input.dataset.workSelect);
      input.disabled = queueActionSaving || !progressState.dailyReady || !current.some(item => selectionKey(item) === input.dataset.workSelect);
    });
  }

  async function deleteItems(items) {
    if (queueActionSaving || progressState.queueStartSaving || !progressState.dailyReady || progressState.queueDay !== $('#progress-date').value) return;
    const chosen = items.filter(selectable);
    if (!chosen.length) return;
    const label = chosen.length === 1 ? `“${chosen[0].filename}”` : `所选 ${chosen.length} 张回单`;
    if (!window.confirm(`确定删除${label}？已有识别记录及其当天同名记录、关联页也会删除，未识别图片将移出队列。此操作无法撤销。`)) return;
    queueActionSaving = true;
    progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    renderWorkbenchRows();
    try {
      const jobs = chosen.filter(item => item.job?.id);
      const removed = jobs.length ? await services.queue.remove(jobs.map(item => item.job.id)) : {deleted_ids: [], kept_ids: [], result_ids: []};
      const ids = [...new Set([...(removed.result_ids || []), ...chosen.filter(item => !item.job?.id && item.id).map(item => item.id)])];
      if (ids.length) await services.records.remove(ids);
      selected.clear();
      toast(removed.kept_ids?.length ? `已删除可删除的任务；${removed.kept_ids.length} 张已开始识别，暂时保留。` : '已删除所选回单。', 'success');
    } catch (error) { toast(`删除未全部完成：${error.message}，请刷新后重试。`, 'danger'); }
    finally {
      queueActionSaving = false;
      recordsState.recordsStale = reportState.reportStale = true;
      resultsState.resultRevision = (resultsState.resultRevision || 0) + 1;
      // Force a fresh record query; a cached result would reappear without its job.
      await loadDailyResults().catch(error => toast(error.message, 'danger'));
      renderWorkbenchRows();
    }
  }

  async function manageQueued(ids, action) {
    if (!ids.length || queueActionSaving || progressState.queueStartSaving || !progressState.dailyReady || progressState.queueDay !== $('#progress-date').value) return;
    const day = $('#progress-date').value;
    queueActionSaving = true; progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    renderWorkbenchRows();
    try {
      await services.queue.manage(ids, action);
      selected.clear();
      if (action === 'withdraw' && $('#progress-date').value === day) setWorkFilter('ready');
      toast(action === 'remove' ? '已移出待开始任务，已有回单记录保留。' : action === 'withdraw' ? '已取消排队，文件保留在本地，可再次开始识别。' : '已优先安排，当前任务结束后处理。', 'success');
    } catch (error) { toast(error.message, 'danger'); }
    finally {
      queueActionSaving = false;
      await loadDailyResults({refreshRecords: false}).catch(error => toast(error.message, 'danger'));
      renderWorkbenchRows();
    }
  }

  function renderWorkbenchRows() {
    if (progressState.workFilters !== null && progressState.workFilters !== undefined) {
      progressState.workFilters = workFilterValues(progressState.workFilters);
    } else if (!Object.hasOwn(WORK_FILTERS, progressState.workFilter)) {
      setWorkFilter('all');
    }
    const filters = activeWorkFilters();
    const allItems = progressState.workItems || [];
    drawer.syncBatches(allItems);
    const items = allItems.filter(item => !progressState.taskBatch || item.job?.task_id === progressState.taskBatch);
    const visible = orderedQueue(items, ReceiptWorkbench).filter(item =>
      matchesOverview(item, filters, ReceiptWorkbench, '', progressState.workSearch || '')
      && overviewStatus(item, ReceiptWorkbench) !== 'completed');
    if ($('#workbench-needs-review')) $('#workbench-needs-review').checked = !!progressState.workNeedsReview;
    if (filters.length === 2 && filters.includes('queued') && filters.includes('running')) {
      visible.sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running'));
    }
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
    const existing = new Set(shown.filter(selectable).map(selectionKey));
    for (const key of selected) if (!existing.has(key)) selected.delete(key);
    renderOverview(items);
    drawer.renderSummary(allItems, items);
    const label = progressState.workNeedsReview ? '待复核' : filters.length ? (filters.length === 3 && ['ready', 'queued', 'running'].every(value => filters.includes(value))
      ? WORK_FILTERS.processing : filters.map(value => WORK_FILTERS[value]).join('、')) : WORK_FILTERS.all;
    $('#workbench-visible-count').textContent = `${label} ${visible.length} 张`;
    if ($('#workbench-total-label')) $('#workbench-total-label').textContent = `当天共 ${items.length} 张`;
    if ($('#workbench-list-title')) $('#workbench-list-title').textContent = '任务队列';
    if ($('#workbench-page-note')) $('#workbench-page-note').textContent = `已显示 ${shown.length} / ${visible.length} 张`;
    if ($('#workbench-more')) {
      $('#workbench-more').classList.toggle('hidden', shown.length >= visible.length);
      $('#workbench-more').textContent = `再显示 ${Math.min(WORK_PAGE_SIZE, visible.length - shown.length)} 张`;
    }
    syncSelection();
    syncStartRecognition();
    const markup = shown.map(item => renderTaskCard(item, {
      ReceiptWorkbench, importsState, progressState, selected, deleting: queueActionSaving, selectable
    })).join('') || `<div class="task-empty"><strong>${!items.length ? '这一天还没有回单' : `暂无符合条件的${label}单据`}</strong><p>${!items.length ? '选择其他日期，或点击上方“导入回单”。' : '可切换任务状态；识别结果请在回单列表查看。'}</p></div>`;
    if (progressState.queueMarkup !== markup) {
      const root = $('#queue'), active = document.activeElement;
      const focusData = root.contains?.(active) ? Object.entries(active.dataset || {})[0] : null;
      root.innerHTML = markup; progressState.queueMarkup = markup;
      if (focusData) $$('button,input', root).find(node => node.dataset[focusData[0]] === focusData[1])?.focus({preventScroll: true});
    }
    syncSelection();
  }

  function readyJobs() {
    const day = $('#progress-date').value;
    if (!progressState.dailyReady || progressState.queueDay !== day) return [];
    return [...(progressState.queueJobs?.values() || [])].filter(job => job.status === 'ready'
      && job.start_requested !== true && job.id
      && (job.import_date || job.created_at?.slice(0, 10) || progressState.queueDay) === day);
  }

  function syncStartRecognition() {
    const button = $('#queue-start');
    if (!button) return;
    const jobs = readyJobs(), uploading = importsState.batchRunning || importsState.importScanning || importsState.uploadingJobs?.size;
    button.textContent = progressState.queueStartSaving ? '正在开始' : '开始识别';
    button.disabled = !jobs.length || !!uploading || !!progressState.queueStartSaving || !!progressState.queueControlSaving || queueActionSaving;
    button.setAttribute('aria-busy', String(!!progressState.queueStartSaving));
    button.title = uploading ? '请等待当前图片读取和上传完成' : jobs.length ? `开始识别 ${$('#progress-date').value} 的 ${jobs.length} 张待开始回单` : '当天暂无已上传且待开始的回单';
    button.setAttribute('aria-label', `开始识别，${button.title}`);
    const note = $('#queue-start-note');
    if (note) note.textContent = uploading ? '正在读取或上传图片，完成后可开始识别。'
      : jobs.length ? `当天 ${jobs.length} 张回单已上传，等待你点击开始识别。` : '导入的回单先保存在本地，上传完成后点击开始识别。';
    $('#queue-start-remote-note')?.classList.toggle('hidden', !jobs.some(job => job.uses_remote));
  }

  async function startRecognition(selectedOnly = false) {
    syncStartRecognition();
    if ($('#queue-start')?.disabled) return;
    const day = $('#progress-date').value, ids = readyJobs().filter(job => selectedOnly !== true || selected.has(`job:${job.id}`)).map(job => job.id);
    if (!ids.length) return;
    let started = 0, released = 0;
    progressState.queueStartSaving = true;
    progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    syncStartRecognition(); syncSelection(); renderQueueControl();
    try {
      await flushSettings();
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
      progressState.queueStartSaving = false; syncStartRecognition(); syncSelection(); renderQueueControl();
    }
  }

  function renderQueueControl() {
    const control = progressState.queueControl;
    if (!control) return;
    const pausing = control.status === 'pausing';
    $('#queue-control-label').textContent = `所有日期 · ${control.paused ? (pausing ? '暂停中' : '已暂停') : '识别开启'}`;
    $('#queue-control-label').classList.toggle('paused', control.paused);
    $('#queue-toggle').textContent = control.paused ? '继续全部识别' : '暂停全部识别';
    $('#queue-toggle').disabled = !!progressState.queueControlSaving || !!progressState.queueStartSaving;
    $('#queue-toggle').classList.toggle('resume-queue', control.paused);
    $('#queue-toggle').title = control.paused ? '继续查询及所有日期中已点击开始的回单；待开始回单仍保持等待' : '暂停单证通后续查询及后续回单；已发出的请求结束后暂停';
    const notice = $('#queue-pause-notice');
    notice.classList.toggle('hidden', !control.paused);
    notice.textContent = pausing ? `所有日期：已请求暂停查询，已发出的请求结束后不再续查；${control.queued} 张等待继续。`
      : `所有日期共 ${control.queued} 张等待继续。暂停期间可照常复核和导入。`;
    syncStartRecognition();
    syncSelection();
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
    drawer.initialize();
    $('#queue-selection-clear')?.addEventListener('click', () => { selected.clear(); renderWorkbenchRows(); });
    $('#queue-selected-delete')?.addEventListener('click', () => deleteItems(viewState.shownItems.filter(item => selected.has(selectionKey(item)))));
    $('#queue-selected-review')?.addEventListener('click', async () => {
      if (reviewOpening || queueActionSaving || !progressState.dailyReady || progressState.queueDay !== $('#progress-date').value) return;
      const chosen = viewState.shownItems.filter(item => reviewable(item) && selected.has(selectionKey(item)));
      const ids = [...new Set(chosen.map(item => item.id))];
      if (!ids.length) return;
      const first = chosen.find(item => ReceiptWorkbench.category(item) === 'review') || chosen[0];
      reviewOpening = true; syncSelection();
      try { await startReviewScope({kind: 'selected', ids, label: `工作台所选（${ids.length} 张）`}, {recordId: first.id}); }
      catch (error) { toast(error.message, 'danger'); }
      finally { reviewOpening = false; syncSelection(); }
    });
    $('#queue-selected-start')?.addEventListener('click', () => startRecognition(true));
    $('#queue-selected-withdraw')?.addEventListener('click', () => manageQueued(viewState.shownItems.filter(item => item.status === 'queued' && selected.has(selectionKey(item))).map(item => item.job.id), 'withdraw'));
    $('#workbench-needs-review')?.addEventListener('change', event => {
      progressState.workNeedsReview = event.target.checked;
      setWorkFilters([]);
      progressState.workVisibleLimit = WORK_PAGE_SIZE;
      selected.clear(); renderWorkbenchRows();
    });
    $('#workbench-select-all')?.addEventListener('change', event => {
      if (queueActionSaving || !progressState.dailyReady) return;
      viewState.shownItems.filter(selectable).forEach(item => event.target.checked ? selected.add(selectionKey(item)) : selected.delete(selectionKey(item)));
      renderWorkbenchRows();
    });
    $('#queue').addEventListener('change', event => {
      const key = event.target.dataset.workSelect;
      if (!key || queueActionSaving || !progressState.dailyReady || !viewState.shownItems.some(item => selectionKey(item) === key && selectable(item))) return;
      event.target.checked ? selected.add(key) : selected.delete(key);
      renderWorkbenchRows();
    });
    $('#workbench-search')?.addEventListener('input', event => {
      progressState.workSearch = event.target.value; selected.clear();
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
      progressState.workNeedsReview = false;
      progressState.workFilterChosen = true;
      progressState.workVisibleLimit = WORK_PAGE_SIZE;
      renderWorkbenchRows();
      $('.workbench-table-wrap')?.scrollTo({top:0});
    }));

    $('#workbench-more')?.addEventListener('click', () => {
      progressState.workVisibleLimit = (progressState.workVisibleLimit || WORK_PAGE_SIZE) + WORK_PAGE_SIZE;
      renderWorkbenchRows();
    });

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

    async function handleTaskAction(event) {
      const view = event.target.closest('[data-work-view]');
      if (view) { if (!view.disabled) await openResultPreview(Number(view.dataset.workView), view); return; }
      const process = event.target.closest('[data-process-job], [data-process-result]');
      if (process) {
        openProcessHistory({
          jobId: process.dataset.processJob || '',
          resultId: process.dataset.processResult || '',
          filename: process.dataset.filename || ''
        });
        return;
      }
      if (!progressState.dailyReady || progressState.queueDay !== $('#progress-date').value || queueActionSaving) return;
      const deleteButton = event.target.closest('[data-work-delete]');
      if (deleteButton) {
        const item = viewState.shownItems.find(item => selectionKey(item) === deleteButton.dataset.workDelete);
        if (item && !deleteButton.disabled) await deleteItems([item]);
        return;
      }
      const adjustment = event.target.closest('[data-job-withdraw], [data-job-prioritize]');
      if (adjustment) {
        if (adjustment.disabled) return;
        await manageQueued([adjustment.dataset.jobWithdraw || adjustment.dataset.jobPrioritize], adjustment.dataset.jobWithdraw ? 'withdraw' : 'prioritize');
        return;
      }
      const start = event.target.closest('[data-job-start]');
      if (start) {
        const id = start.dataset.jobStart, job = progressState.queueJobs?.get(id);
        if (!job || start.disabled || progressState.workPendingJobs?.has(id)) return;
        progressState.workPendingJobs ||= new Set(); progressState.workPendingJobs.add(id); start.disabled = true;
        try {
          await flushSettings();
          await (services.queue.start([id]));
          setWorkFilter('processing'); progressState.workVisibleLimit = WORK_PAGE_SIZE;
          await loadDailyResults({refreshRecords: false});
        } catch (error) { toast(error.message, 'danger'); start.disabled = false; }
        finally { progressState.workPendingJobs.delete(id); }
        return;
      }
      const retry = event.target.closest('[data-work-retry]');
      if (retry) { await retryOne(Number(retry.dataset.workRetry), retry); return; }
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
          await flushSettings();
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
    }
    $('#queue').addEventListener('click', handleTaskAction);
    $('#records-body')?.addEventListener('click', handleTaskAction);

    $('#progress-date').value = $('#import-date')?.value || localToday();
    // Cached templates can keep using their native picker until refreshed.
    if ($('#progress-date').type === 'hidden') calendar.initialize();

    $('#progress-date').addEventListener('change', changeProcessingDay);
    $('#refresh-progress').addEventListener('click', changeProcessingDay);

    $('#progress-prev').addEventListener('click', () => shiftProcessingDay(-1));
    $('#progress-next').addEventListener('click', () => shiftProcessingDay(1));
    $('#progress-today').addEventListener('click', () => { $('#progress-date').value = localToday(); changeProcessingDay(); });
    syncStartRecognition();

  }

  return {initialize, drawer, showQueuedRetry, renderTask, renderWorkbenchRows, renderQueueControl, syncStartRecognition, startRecognition, loadDailyResults, changeProcessingDay, shiftProcessingDay};
}
