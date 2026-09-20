import {localToday, filenameParts, escapeHtml} from './ui.mjs';
import {workbenchAttention} from './progress_presentation.mjs';
import {createImportCalendar} from './import_calendar.mjs';
import {confirmInline} from './inline_confirm.mjs';

const WORK_PAGE_SIZE = 50;
const START_BATCH_SIZE = 5000;
import {WORK_FILTERS, DOCUMENT_TYPES, overviewStatus, overviewType, overviewCustomer, overviewCounts, matchesOverview, selectionKey, deletionTarget} from './workbench_overview.mjs';

export function createProgress({
  environment, ui, importsState, progressState, recordsState, reportState, resultsState, reviewState,
  api, ReceiptWorkbench, ReceiptQueue, retryOne, startReviewScope, openProcessHistory = () => {}
}) {
  const {document, window, location, localStorage, FormData} = environment;
  const {$, $$, toast} = ui;
  const selected = new Set();
  let shownItems = [], deleting = false;
  const calendar = createImportCalendar({environment, ui, api, prefix: 'progress-calendar', popup: '#progress-calendar',
    readDate: () => $('#progress-date').value,
    selectDate: day => { $('#progress-date').value = day; changeProcessingDay(); }});

  async function showQueuedRetry(task) {
    $('#progress-date').value = (task.created_at || '').slice(0, 10) || localToday();
    try { localStorage.setItem('receipt-progress-date', $('#progress-date').value); } catch (_) {}
    progressState.workFilter = 'processing';
    progressState.workVisibleLimit = WORK_PAGE_SIZE;
    location.hash = 'progress';
    toast('已加入后台识别队列，可以关闭页面。', 'success');
    await loadDailyResults();
  }

  function renderTask(task) {
    const percent = task.total ? Math.min(100, Math.round(task.completed / task.total * 100)) : 0;
    const finished = task.total > 0 && task.completed >= task.total;
    const summary = !task.total ? '暂无回单' : `已处理 ${task.completed} / ${task.total} 张${task.ready ? ` · ${task.ready} 张待开始` : finished ? ' · 识别已结束' : ''}`;
    $('#task-summary').textContent = `${summary}${task.error_message ? ` · ${task.error_message}` : ''}`;
    $('#task-progress').style.width = `${percent}%`;
    $('#task-progress').parentElement.setAttribute('aria-valuenow', percent);
    $('#task-summary').dataset.status = finished && task.pending_review ? 'review' : finished ? 'done' : 'running';
    // A workbench request may finish after navigation; it must not replace the
    // shared entry's current-filter scope on the records page.
    if (document.body.dataset.activePage !== 'progress') return;
    const count = Number(task.pending_review) || 0;
    $('#review-entry-count').textContent = count;
    $('#review-entry-count').classList.toggle('hidden', count === 0);
    $('#open-batch-review').classList.toggle('has-pending', count > 0);
    const scopeDay = $('#progress-date').value || localToday();
    $('#open-batch-review').dataset.reviewDay = scopeDay;
    $('#open-batch-review').setAttribute('aria-label', `人工复核，${scopeDay}，${count} 张待复核`);
    $('#open-batch-review').title = count ? `复核 ${scopeDay} 当天的待复核回单` : `${scopeDay} 当天暂无待复核回单`;
  }

  function renderOverview(items) {
    const counts = overviewCounts(items, ReceiptWorkbench);
    const percent = counts.total ? Math.round(counts.completed / counts.total * 1000) / 10 : 0;
    for (const [id, value] of Object.entries({'overview-completed': counts.completed.toLocaleString(), 'overview-total': counts.total.toLocaleString(), 'overview-percent': `${percent}%`})) {
      if ($(`#${id}`)) $(`#${id}`).textContent = value;
    }
    $('#task-progress').style.width = `${percent}%`;
    $('#task-progress').parentElement.setAttribute('aria-valuenow', percent);
    if ($('#overview-remaining')) $('#overview-remaining').textContent = `剩余 ${counts.ready + counts.running} 张 · 待开始 ${counts.ready} 张 · 处理中 ${counts.running} 张`;
    if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = !counts.total ? '暂无单据' : progressState.queueControl?.paused ? (progressState.queueControl.status === 'pausing' ? '暂停中' : '已暂停') : counts.running ? '正在识别' : counts.ready ? '等待处理' : '识别已结束';
    const types = items.filter(item => item.status !== 'cancelled');
    const customers = [...new Set(types.map(overviewCustomer))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    if ($('#workbench-type-counts')) $('#workbench-type-counts').innerHTML = '<span>按客户</span>' + customers.map(customer => `<button type="button" data-work-type="${escapeHtml(customer)}" aria-pressed="${progressState.workType === customer}">${escapeHtml(customer)} <b>${types.filter(item => overviewCustomer(item) === customer).length}</b></button>`).join('') + '<button type="button" data-work-type="" class="workbench-clear-type">全部客户</button>';
  }

  function selectable(item) { return !!deletionTarget(item) && !importsState.uploadingJobs?.has(item.job?.id); }
  function syncSelection() {
    const current = shownItems.filter(selectable), all = $('#workbench-select-all');
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
      input.disabled = deleting || !progressState.dailyReady || !shownItems.some(item => selectionKey(item) === input.dataset.workSelect && selectable(item));
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
        const result = await api('/api/jobs/cancel', {method: 'POST', json: {ids: group.map(item => item.job.id)}});
        const deleted = new Set(result.deleted_ids || []);
        for (const item of group) if (deleted.has(item.job.id)) {
          removed++; selected.delete(selectionKey(item));
          if (item.id) recordIds.add(Number(item.id));
        }
        if (result.kept_ids?.length) failure = '部分任务已完成，已保留，请刷新后重新选择。';
      }
      const ids = [...recordIds];
      for (let offset = 0; offset < ids.length; offset += 2000) {
        const result = await api('/api/results/delete', {method: 'POST', json: {ids: ids.slice(offset, offset + 2000)}});
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
    if (!Object.hasOwn(WORK_FILTERS, progressState.workFilter)) progressState.workFilter = 'review';
    const items = progressState.workItems || [], visible = ReceiptWorkbench.ordered(items).filter(item => matchesOverview(item, progressState.workFilter, ReceiptWorkbench, progressState.workType || '', progressState.workSearch || ''));
    const limit = progressState.workVisibleLimit || WORK_PAGE_SIZE, shown = visible.slice(0, limit);
    $$('[data-work-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.workFilter === progressState.workFilter)));
    $$('[data-work-count]').forEach(label => label.textContent = items.filter(item => matchesOverview(item, label.dataset.workCount, ReceiptWorkbench)).length);
    shownItems = shown;
    const existing = new Set(items.filter(selectable).map(selectionKey));
    for (const key of selected) if (!existing.has(key)) selected.delete(key);
    renderOverview(items);
    const label = WORK_FILTERS[progressState.workFilter];
    $('#workbench-visible-count').textContent = `${label} ${visible.length} 张`;
    if ($('#workbench-total-label')) $('#workbench-total-label').textContent = `当天共 ${items.length} 张`;
    if ($('#workbench-list-title')) $('#workbench-list-title').textContent = `${label}单据`;
    if ($('#workbench-reason-heading')) $('#workbench-reason-heading').textContent = ['processing', 'ready', 'running'].includes(progressState.workFilter) ? '处理进度' : '需要关注 / 核验结果';
    if ($('#workbench-page-note')) $('#workbench-page-note').textContent = `已显示 ${shown.length} / ${visible.length} 张`;
    if ($('#workbench-more')) {
      $('#workbench-more').classList.toggle('hidden', shown.length >= visible.length);
      $('#workbench-more').textContent = `再显示 ${Math.min(WORK_PAGE_SIZE, visible.length - shown.length)} 张`;
    }
    syncStartReview();
    syncStartRecognition();
    const markup = shown.map(item => {
      const job = item.job, record = item.record || {}, uploading = job && importsState.uploadingJobs?.has(job.id);
      const category = ReceiptWorkbench.category(item);
      const showActivity = uploading || (item.status === 'running' && job?.progress?.stage !== 'paused');
      let attention = workbenchAttention(item, {workbench: ReceiptWorkbench, paused: progressState.queueControl?.paused, uploading});
      const isVerdict = ['passed', 'rejected'].includes(category);
      if (isVerdict) attention = {primary: `核验${WORK_FILTERS[category]}`, secondary: record.review_status || '', danger: category === 'rejected'};
      const dateCheck = record.date_check || {}, sealCheck = record.seal_check || {};
      const expectedDate = record.fields?.['要求到货'] || dateCheck.required || '';
      const order = record.fields?.['客户订单号'] || '';
      // Every state shares one visual language: a status badge plus a
      // verification badge summary, so the eye lands on what needs action.
      const badge = (label, value, status) => {
        const tone = ['不匹配', '部分匹配', '部分识别'].includes(status) ? 'is-danger' : status === '匹配' ? 'is-ok' : 'is-muted';
        return `<span class="work-pass-badge ${tone}" title="${escapeHtml(`${label}：${value}（${status}）`)}"><em>${label}</em><b>${escapeHtml(value)}</b></span>`;
      };
      const checkBadges = (leading = '') => {
        const dateStatus = ReceiptWorkbench.checkStatus(record, 'date');
        const sealStatus = ReceiptWorkbench.checkStatus(record, 'seal');
        const signatureStatus = ReceiptWorkbench.checkStatus(record, 'signature');
        // Mismatched dates repeat the year: keep the first full date once.
        const displayedActual = dateCheck.actual_display || dateCheck.actual || '';
        const shortActual = dateCheck.actual && expectedDate && dateCheck.actual.slice(0, 4) === expectedDate.slice(0, 4) ? dateCheck.actual.slice(5) : displayedActual;
        const dateValue = dateCheck.actual ? (dateCheck.actual !== expectedDate ? `${expectedDate || '—'} → ${shortActual}` : displayedActual) : (displayedActual || expectedDate || '—');
        const badges = [badge('日期', dateValue, dateStatus), badge('印章', sealStatus, sealStatus)];
        if (signatureStatus !== '未识别') badges.push(badge('签名', signatureStatus, signatureStatus));
        return `<div class="work-pass-badges">${leading}${badges.join('')}</div>`;
      };
      const attentionBadge = (tone, limit = 90) => {
        const full = String(attention.primary || '');
        const firstLine = full.split('\n')[0].trim();
        const display = firstLine.length > limit ? `${firstLine.slice(0, limit)}…` : firstLine;
        return `<span class="work-attention-badge ${tone}" title="${escapeHtml(full)}">${showActivity && category === 'processing' ? '<i class="status-pulse" aria-hidden="true"></i>' : ''}${escapeHtml(display)}</span>`;
      };
      const metaLine = (text, danger) => text ? `<small class="work-pass-meta${danger ? ' is-danger' : ''}" title="${escapeHtml(text)}">${escapeHtml(text)}</small>` : '';
      const completionTime = isVerdict ? (record.updated_at || record.created_at || '').replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '') : '';
      const verdictMeta = completionTime ? `<small class="work-pass-meta" title="完成于 ${escapeHtml(completionTime)}">${escapeHtml(completionTime)}</small>` : '';
      // Each row spreads over dedicated columns — status / results / note — so a
      // row stays one line tall instead of stacking pill, badges and notes.
      // The status column keeps a single pill: review status lives elsewhere.
      let resultHtml = '', noteHtml = '';
      if (isVerdict) {
        resultHtml = checkBadges();
        noteHtml = verdictMeta;
      } else if (category === 'review') {
        // The mismatch reason sits in the note column so the three check
        // badges keep one line in the results column.
        resultHtml = checkBadges();
        noteHtml = `${attentionBadge(attention.danger ? 'is-danger' : 'is-warning')}${metaLine(attention.secondary, attention.secondaryDanger)}`;
      } else if (category === 'failed') {
        // Failed rows carry no usable checks yet — the error badge and the note
        // already say everything, so the row stays one line tall.
        resultHtml = `<div class="work-pass-badges">${attentionBadge('is-danger', 64)}</div>`;
        noteHtml = metaLine(attention.secondary || '可以重新尝试识别', true);
      } else {
        const pillLabel = WORK_FILTERS[overviewStatus(item, ReceiptWorkbench)] || '';
        const processingBadge = attention.primary && attention.primary !== pillLabel ? attentionBadge('is-info') : '';
        resultHtml = processingBadge ? `<div class="work-pass-badges">${processingBadge}</div>` : '';
        noteHtml = metaLine(attention.secondary, false);
      }
      let action = '';
      if (job?.status === 'failed') action = `<button class="mini-button" data-job-retry="${escapeHtml(job.id)}">重试</button>`;
      else if (category === 'failed' && item.id) action = `<button class="mini-button" data-work-retry="${escapeHtml(item.id)}">重试</button>`;
      else if (job?.status === 'ready' && job.id) action = `<button class="mini-button" data-job-start="${escapeHtml(job.id)}">开始识别</button>`;
      else if (job?.status === 'awaiting_upload' && !uploading) action = `<button class="mini-button" data-job-upload="${escapeHtml(job.id)}">补传图片</button>`;
      else if (item.id && item.status === 'succeeded') action = `<button class="mini-button" ${category === 'review' ? 'data-work-review' : 'data-work-view'}="${escapeHtml(item.id)}">${category === 'review' ? '开始复核' : '查看'}</button>`;
      const processAction = job?.id
        ? `<button class="mini-button process-button" data-process-job="${escapeHtml(job.id)}" data-process-result="${escapeHtml(item.id || '')}" data-filename="${escapeHtml(item.filename)}">流程</button>`
        : item.id ? `<button class="mini-button process-button" data-process-result="${escapeHtml(item.id)}" data-filename="${escapeHtml(item.filename)}">流程</button>` : '';
      const cancelAction = job?.id && ['awaiting_upload', 'ready', 'queued', 'running'].includes(job.status)
        ? `<button class="mini-button row-cancel" data-job-cancel="${escapeHtml(job.id)}">取消</button>` : '';
      const actionGroup = action || processAction || cancelAction ? `<div class="work-row-actions">${action}${processAction}${cancelAction}</div>` : '';
      const documentType = overviewType(item);
      const typeTag = ['unclassified', 'unknown'].includes(documentType) ? '' : `<span class="workbench-row-type">${DOCUMENT_TYPES[documentType]}</span>`;
      const customerName = record.fields?.['客户名称'] || '';
      const fileSubtitle = [customerName, order ? `订单 ${order}` : ''].filter(Boolean).join(' · ');
      return `<tr class="work-item work-item-${category}"><td><input type="checkbox" class="workbench-row-check" data-work-select="${escapeHtml(selectionKey(item))}" aria-label="选择 ${escapeHtml(item.filename)}" ${selected.has(selectionKey(item)) ? 'checked' : ''} ${!selectable(item) || deleting ? 'disabled' : ''}><span class="file-line"><strong title="${escapeHtml(item.filename)}">${escapeHtml(filenameParts(item.filename).name)}</strong>${fileSubtitle ? `<small title="${escapeHtml(fileSubtitle)}">${escapeHtml(fileSubtitle)}</small>` : ''}</span></td><td class="status-cell"><span class="workbench-row-status" data-status="${overviewStatus(item, ReceiptWorkbench)}">${WORK_FILTERS[overviewStatus(item, ReceiptWorkbench)] || '已取消'}</span>${typeTag}</td><td class="attention-cell">${resultHtml}</td><td class="note-cell">${noteHtml}</td><td>${actionGroup}</td></tr>`;
    }).join('') || `<tr><td colspan="5" class="empty-state"><strong>${!items.length ? '这一天还没有回单' : `暂无符合条件的${label}单据`}</strong><p>${!items.length ? '选择其他日期，或点击上方“导入回单”。' : progressState.workFilter === 'review' && items.some(item => ReceiptWorkbench.category(item) === 'processing') ? '识别完成后，需要确认的回单会出现在这里。' : '可切换其他状态，或查看当天全部记录。'}</p></td></tr>`;
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
    queueToggle?.classList.remove('hidden');
    cancelAll?.classList.toggle('hidden', progressState.workFilter !== 'processing');
    if (cancelAll) {
      const jobs = cancelableJobs();
      cancelAll.disabled = !jobs.length || !!progressState.queueCancelSaving || !!progressState.queueStartSaving || !!progressState.queueControlSaving;
      cancelAll.textContent = progressState.queueCancelSaving ? '正在取消…' : `全部取消（${jobs.length}）`;
      cancelAll.title = jobs.length ? `删除当天 ${jobs.length} 张待开始或处理中的回单` : '当天没有可取消的待处理回单';
      cancelAll.setAttribute('aria-busy', String(!!progressState.queueCancelSaving));
    }
    if (!button) return;
    button.classList.toggle('hidden', progressState.workFilter !== 'review');
    const ready = progressState.dailyReady && progressState.queueDay === $('#progress-date').value;
    if (progressState.workFilter === 'processing') {
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
      const result = await api('/api/jobs/cancel', {method: 'POST', json: {ids}});
      const count = Number(result.deleted_ids?.length) || 0;
      toast(`已取消并删除 ${count} 张待处理回单。`, 'success');
      progressState.workFilter = 'processing';
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
        const result = await api('/api/jobs/start', {method: 'POST', json: {ids: group}});
        started += Number(result.started) || 0; released += group.length;
        // A day change during the request must keep its own list and controls.
        if (progressState.queueDay === day && $('#progress-date').value === day) {
          for (const job of result.items || []) if (progressState.queueJobs.has(job.id)) progressState.queueJobs.set(job.id, job);
          progressState.workFilter = 'processing'; progressState.workVisibleLimit = WORK_PAGE_SIZE;
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

  async function loadDailyResults({refreshRecords = true} = {}) {
    progressState.dailyAttemptAt = Date.now();
    const day = $('#progress-date').value || localToday();
    $('#progress-date').value = day;
    calendar.sync();
    const reviewEntry = $('#open-batch-review');
    if (document.body.dataset.activePage === 'progress' && reviewEntry.dataset.reviewDay !== day) {
      reviewEntry.dataset.reviewDay = day;
      reviewEntry.setAttribute('aria-label', `人工复核，${day}，当天全部`);
      reviewEntry.title = `复核 ${day} 当天的回单`;
      reviewEntry.classList.remove('has-pending');
      $('#review-entry-count').textContent = '';
      $('#review-entry-count').classList.add('hidden');
    }
    const requestId = progressState.dailyRequest = (progressState.dailyRequest || 0) + 1;
    const previousDay = progressState.queueDay, newDay = previousDay !== day;
    if (newDay) {
      selected.clear(); shownItems = [];
      if ($('#overview-completed')) $('#overview-completed').textContent = '—';
      if ($('#overview-total')) $('#overview-total').textContent = '—';
      if ($('#overview-percent')) $('#overview-percent').textContent = '—';
      if ($('#overview-remaining')) $('#overview-remaining').textContent = '正在加载当天数据…';
      if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = '加载中';
      if ($('#workbench-type-counts')) $('#workbench-type-counts').innerHTML = '';
      $$('[data-work-count]').forEach(label => label.textContent = '—');
      progressState.dailyReady = false;
      syncSelection();
      progressState.workVisibleLimit = WORK_PAGE_SIZE;
      $('#task-section').classList.add('hidden');
      $('#task-summary').textContent = '正在加载';
      $('#task-summary').dataset.status = 'loading';
      $('#task-progress').style.width = '0%';
      $('#task-progress').parentElement.setAttribute('aria-valuenow', 0);
      if ($('#workbench-total-label')) $('#workbench-total-label').textContent = '';
      $('#progress-note').textContent = `正在加载 ${day} 的回单…`;
    }
    syncStartReview();
    syncStartRecognition();
    $('#progress-note').dataset.tone = '';
    $('#task-section').setAttribute('aria-busy', 'true');
    try {
      const [jobs, control] = await Promise.all([api(`/api/queue?import_date=${encodeURIComponent(day)}`), api('/api/queue/control')]);
      const signature = jobs.filter(job => ['succeeded', 'failed', 'cancelled'].includes(job.status)).map(job => `${job.id}:${job.status}:${job.finished_at}`).join('|');
      const cached = progressState.progressRecordCache;
      const reuse = !refreshRecords && cached?.day === day && cached.signature === signature && Date.now() - cached.at < 30000;
      const records = reuse ? cached.records : await api(`/api/daily-results?import_date=${encodeURIComponent(day)}&include_queue=1`);
      if (requestId !== progressState.dailyRequest || $('#progress-date').value !== day) return;
      if (!reuse) progressState.progressRecordCache = {day, signature, records, at: Date.now()};
      progressState.queueControl = control;
      renderQueueControl();
      progressState.queueJobs = new Map(jobs.map(job => [job.id, job]));
      const items = ReceiptQueue.rows(records, jobs), counts = ReceiptQueue.counts(items);
      counts.pending_review = items.filter(item => ReceiptWorkbench.category(item) === 'review').length;
      counts.passed = items.filter(item => ReceiptWorkbench.category(item) === 'passed').length;
      if (!progressState.workInitialFilterResolved) {
        if (!progressState.workFilterChosen && progressState.workFilter === 'review' && counts.ready && !counts.pending_review) progressState.workFilter = 'processing';
        progressState.workInitialFilterResolved = true;
      }
      if (control.paused) counts.status = control.status === 'pausing' ? '暂停中' : '已暂停';
      $('#task-section').classList.remove('hidden');
      renderTask(counts);
      // Keep visible receipts in place when a polling response adds newer jobs.
      const key = item => item.id ? `record:${item.id}` : `job:${item.job?.id}`;
      const ranks = new Map((newDay ? [] : progressState.workItems || []).map((item, index) => [key(item), index]));
      progressState.workItems = items.sort((a, b) => (ranks.get(key(a)) ?? Infinity) - (ranks.get(key(b)) ?? Infinity));
      progressState.queueDay = day;
      progressState.dailyReady = true;
      renderWorkbenchRows();
      $('#progress-note').textContent = importsState.batchRunning || importsState.uploadingJobs?.size ? '正在上传，请保持页面打开。' : '';
      $('#refresh-progress').textContent = '刷新';
      if (previousDay === day && progressState.queueSignature !== undefined && progressState.queueSignature !== signature) {
        recordsState.recordsStale = reportState.reportStale = true;
        resultsState.resultRevision = (resultsState.resultRevision || 0) + 1;
      }
      progressState.queueDay = day; progressState.queueSignature = signature;
    } catch (error) {
      if (requestId !== progressState.dailyRequest || $('#progress-date').value !== day) return;
      progressState.dailyReady = false;
      syncSelection();
      if ($('#workbench-state-pill')) $('#workbench-state-pill').textContent = '更新失败';
      syncStartReview();
      syncStartRecognition();
      if (newDay) { $('#task-summary').textContent = '暂时无法加载'; $('#task-summary').dataset.status = 'error'; }
      $('#progress-note').dataset.tone = 'danger';
      $('#progress-note').textContent = newDay ? `${day} 的回单加载失败，请重试。` : '更新失败，当前显示上次成功加载的回单，请重试。';
      $('#refresh-progress').textContent = '重试';
      throw error;
    } finally {
      if (requestId === progressState.dailyRequest) $('#task-section').removeAttribute('aria-busy');
    }
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
      shownItems.filter(selectable).forEach(item => event.target.checked ? selected.add(selectionKey(item)) : selected.delete(selectionKey(item)));
      renderWorkbenchRows();
    });
    $('#queue').addEventListener('change', event => {
      const key = event.target.dataset.workSelect;
      if (!key || deleting || !progressState.dailyReady || !shownItems.some(item => selectionKey(item) === key && selectable(item))) return;
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
      progressState.workFilter = button.dataset.workFilter;
      if (progressState.workFilter === 'all') { progressState.workType = ''; progressState.workSearch = ''; if ($('#workbench-search')) $('#workbench-search').value = ''; }
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
      if (progressState.workFilter === 'processing') { await startRecognition(); return; }
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
        progressState.queueControl = await api('/api/queue/control', {method:'POST', json:{paused:!progressState.queueControl.paused}});
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
          await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, {method: 'POST'});
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
          await api('/api/jobs/start', {method: 'POST', json: {ids: [id]}});
          progressState.workFilter = 'processing'; progressState.workVisibleLimit = WORK_PAGE_SIZE;
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
      if (job.uses_remote && !(button.dataset.jobUpload && job.start_requested === false)
          && !await confirmInline({environment, anchor: button, message: '此任务使用已保存的远程识别配置，会将完整回单上传至所选服务。'})) return;
      if (button.dataset.jobRetry) {
        progressState.workPendingJobs ||= new Set(); progressState.workPendingJobs.add(id);
        button.disabled = true;
        try {
          await api(`/api/jobs/${encodeURIComponent(id)}/retry`, {method: 'POST'});
          progressState.workFilter = 'processing'; progressState.workVisibleLimit = WORK_PAGE_SIZE;
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
        try { await api(`/api/jobs/${encodeURIComponent(id)}/upload`, {method: 'POST', body: form}); }
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
