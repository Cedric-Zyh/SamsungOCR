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
import {WORK_FILTERS, matchesOverview, workFilterValues, selectionKey, overviewStatus, orderedQueue} from './overview.mjs';

export function createProgress({
  environment, ui, importsState, progressState, recordsState, reportState, resultsState,
  api, services = createApiClients(api), ReceiptWorkbench, ReceiptQueue, retryOne, startReviewScope,
  openProcessHistory = () => {}, openResultPreview = () => {}
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
    renderQueueControl, renderTask, renderWorkbenchRows
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

  function reviewable(item) { return Number.isSafeInteger(item.id) && item.id > 0 && overviewStatus(item, ReceiptWorkbench) === 'completed'; }
  function selectable(item) {
    if (importsState.uploadingJobs?.has(item.job?.id) || progressState.workPendingJobs?.has(item.job?.id)) return false;
    return reviewable(item) || !!item.job?.id && ['ready', 'queued'].includes(item.status);
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
    const items = progressState.workItems || [], visible = orderedQueue(items, ReceiptWorkbench).filter(item =>
      matchesOverview(item, filters, ReceiptWorkbench, '', progressState.workSearch || '')
      && (progressState.workNeedsReview ? ReceiptWorkbench.category(item) === 'review'
        : filters.includes('completed') || overviewStatus(item, ReceiptWorkbench) !== 'completed'));
    if ($('#workbench-needs-review')) $('#workbench-needs-review').checked = !!progressState.workNeedsReview;
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
    const markup = shown.map(item => renderWorkbenchRow(item, {
      ReceiptWorkbench, importsState, progressState, selected, deleting: queueActionSaving, selectable
    })).join('') || `<tr><td colspan="5" class="empty-state"><strong>${!items.length ? '这一天还没有回单' : `暂无符合条件的${label}单据`}</strong><p>${!items.length ? '选择其他日期，或点击上方“导入回单”。' : '可以切换任务状态或关闭“待复核”筛选；选择“已完成”可查看全部处理结果。'}</p></td></tr>`;
    if (progressState.queueMarkup !== markup) { $('#queue').innerHTML = markup; progressState.queueMarkup = markup; }
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
    button.textContent = progressState.queueStartSaving ? '正在开始…' : `开始全部待开始（${jobs.length}）`;
    button.disabled = !jobs.length || !!uploading || !!progressState.queueStartSaving || !!progressState.queueControlSaving || queueActionSaving;
    button.setAttribute('aria-busy', String(!!progressState.queueStartSaving));
    button.title = uploading ? '请等待当前图片读取和上传完成' : jobs.length ? `开始识别 ${$('#progress-date').value} 的 ${jobs.length} 张待开始回单` : '当天暂无已上传且待开始的回单';
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
    $('#queue-toggle').textContent = control.paused ? '继续队列' : '暂停队列';
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
    $('#queue-selection-clear')?.addEventListener('click', () => { selected.clear(); renderWorkbenchRows(); });
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

    $('#queue').addEventListener('click', async event => {
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
      const remove = event.target.closest('[data-job-remove]');
      if (remove) {
        const job = progressState.queueJobs?.get(remove.dataset.jobRemove);
        if (remove.disabled || !job || job.status !== 'ready') return;
        if (window.confirm(`将“${job.filename}”移出队列，并清理这次导入的临时文件？已有回单记录会保留。`)) await manageQueued([job.id], 'remove');
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

  return {initialize, showQueuedRetry, renderTask, renderWorkbenchRows, renderQueueControl, syncStartRecognition, startRecognition, loadDailyResults, changeProcessingDay, shiftProcessingDay};
}
