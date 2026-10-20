import {createState} from '../core/state.mjs';
import {createUi} from '../core/ui.mjs';
import {createApi, createApiClients} from '../core/api.mjs';
import {createRecords} from '../records/records.mjs';
import {createImports} from '../imports/imports.mjs';
import {createRecognitionPlan} from '../imports/recognition_plan.mjs';
import {createReview} from '../review/review.mjs';
import {createReviewEvidence} from '../review/review_evidence.mjs';
import {createReviewQueue} from '../review/review_queue.mjs';
import {createProgress} from '../workbench/workbench.mjs';
import {createResultPreview} from '../workbench/result_preview.mjs';
import {createReport} from '../report/report.mjs';
import {createNavigation} from './navigation.mjs';
import {createProcessHistory} from '../records/process_history.mjs';
import {createRetentionSettings} from '../imports/retention_settings.mjs';
import {createRemoteSettings} from '../imports/remote_settings.mjs';
import {createPolling} from '../core/polling.mjs';
import {createSealCenter} from '../seals/center.mjs';

/** Composition root: controllers communicate through explicit callbacks.
 * Constructing an application performs no requests or event registration. */
export function createApplication(environment, options = {}) {
  const {document, window, setTimeout, clearTimeout} = environment;
  const state = options.state || createState();
  const ui = options.ui || createUi(environment);
  const {$, toast} = ui;
  const {records: recordsState, review: reviewState, imports: importsState,
    progress: progressState, report: reportState, navigation: navigationState, results: resultsState} = state;
  const fieldSchema = options.fieldSchema || JSON.parse($('#field-schema').textContent);
  const {ReceiptImport, ReceiptQueue, ReceiptWorkbench} = {...environment, ...options};
  const api = options.api || createApi({fetch: environment.fetch, onResultsChanged: invalidateResultViews});
  const services = options.services || createApiClients(api);
  const seal_center = createSealCenter({ui, services});
  const process_history = createProcessHistory({
    environment,
    ui,
    api,
    services
  });
  const retention_settings = createRetentionSettings({environment, ui, api, services});
  const remote_settings = createRemoteSettings({environment, ui, api});
  const records = createRecords({
    ReceiptWorkbench,
    environment,
    ui,
    recordsState,
    reviewState,
    api,
    services,
    startReviewScope: (...args) => review_queue.startReviewScope(...args),
    retryOne: (...args) => imports.retryOne(...args),
    openProcessHistory: (...args) => process_history.open(...args),
    refreshVisibleResults,
    onDateChanged: day => {
      $('#progress-date').value = day;
      progressState.taskBatch = '';
      progress.loadDailyResults().catch(error => ui.toast(error.message, 'danger'));
    },
    setBatchStep: (...args) => imports.setBatchStep(...args)
  });
  const imports = createImports({
    environment,
    ui,
    importsState,
    recordsState,
    progressState,
    api,
    services,
    ReceiptImport,
    readRecognitionPlan: (...args) => recognition_plan.readRecognitionPlan(...args),
    flushSettings: () => recognition_plan.flushSettings(),
    readAcceptancePolicy: (...args) => recognition_plan.readAcceptancePolicy(...args),
    usesRemotePlan: (...args) => recognition_plan.usesRemotePlan(...args),
    updateRecognitionPlan: (...args) => recognition_plan.updateRecognitionPlan(...args),
    loadDailyResults: (...args) => progress.loadDailyResults(...args),
    syncStartRecognition: (...args) => progress.syncStartRecognition?.(...args),
    openManagedRecords,
    showQueuedRetry: (...args) => progress.showQueuedRetry(...args),
    closeModal: (...args) => review.closeModal(...args)
  });
  const recognition_plan = createRecognitionPlan({
    environment,
    ui,
    importsState,
    services
  });
  const review = createReview({
    environment,
    ui,
    navigationState,
    reviewState,
    api,
    services,
    ReceiptWorkbench,
    fieldSchema,
    fieldEditor: (...args) => review_evidence.fieldEditor(...args),
    renderProductTable: (...args) => review_evidence.renderProductTable(...args),
    renderArtifacts: (...args) => review_evidence.renderArtifacts(...args),
    renderHistory: (...args) => review_evidence.renderHistory(...args),
    renderGroundTruth: (...args) => review_evidence.renderGroundTruth(...args),
    loadReviewQueue: (...args) => review_queue.loadReviewQueue(...args),
    continueReview: (...args) => review_queue.continueReview(...args),
    startReviewScope: (...args) => review_queue.startReviewScope(...args),
    markReviewSaved: (...args) => review_queue.markReviewSaved(...args),
    ensureReviewScope: (...args) => review_queue.ensureReviewScope(...args),
    getReviewScope: () => document.body.dataset.activePage === 'records'
      ? records.getReviewScope('filtered')
      : {kind: 'day', label: `${$('#progress-date').value || ''} · 当天全部`, filters: {import_date: $('#progress-date').value || ''}},
    refreshVisibleResults,
    retryOne: (...args) => imports.retryOne(...args),
    showPage: (...args) => navigation.showPage(...args),
    routePage: (...args) => navigation.routePage(...args)
  });
  const review_evidence = createReviewEvidence({
    environment,
    ui,
    reviewState,
    api,
    services,
    fieldSchema
  });
  const review_queue = createReviewQueue({
    environment,
    ui,
    reviewState,
    api,
    ReceiptWorkbench,
    services,
    canLeaveReview: (...args) => review.canLeaveReview(...args),
    openReview: (...args) => review.openReview(...args),
    showReviewEmpty: (...args) => review.showReviewEmpty(...args),
    showPage: (...args) => navigation.showPage(...args)
  });
  const result_preview = createResultPreview({environment, ui, services, ReceiptWorkbench,
    enterReview: id => review_queue.startReviewScope({kind: 'selected', ids: [id], label: '当前单据'}, {recordId: id})});
  const progress = createProgress({
    environment,
    ui,
    importsState,
    progressState,
    recordsState,
    reportState,
    resultsState,
    reviewState,
    api,
    services,
    ReceiptWorkbench,
    ReceiptQueue,
    openResultPreview: (...args) => result_preview.open(...args),
    openManagedRecords,
    onQueueChanged: () => {
      recordsState.recordsStale = true;
      if (document.body.dataset.activePage === 'records') records.loadRecords({background: true}).catch(error => ui.toast(error.message, 'danger'));
    },
    flushSettings: () => recognition_plan.flushSettings(),
    retryOne: (...args) => imports.retryOne(...args),
    startReviewScope: (...args) => review_queue.startReviewScope(...args),
    openProcessHistory: (...args) => process_history.open(...args)
  });
  const report = createReport({
    environment,
    ui,
    reportState,
    resultsState,
    api,
    services
  });
  const navigation = createNavigation({
    environment,
    ui,
    navigationState,
    recordsState,
    reportState,
    reviewState,
    canLeaveReview: (...args) => review.canLeaveReview(...args),
    dismissReview: (...args) => review.dismissReview(...args),
    openReview: (...args) => review.openReview(...args),
    showReviewEmpty: (...args) => review.showReviewEmpty(...args),
    showImportDialog: (...args) => imports.showImportDialog(...args),
    loadReviewQueue: (...args) => review_queue.loadReviewQueue(...args),
    loadDailyResults: (...args) => progress.loadDailyResults(...args),
    loadRecords: (...args) => records.loadRecords(...args),
    loadReport: (...args) => report.loadReport(...args),
    loadSealCenter: (...args) => seal_center.load(...args)
  });

  function openManagedRecords(day, taskId = '', batchLabel = '当前导入批次') {
    records.openDayRecords(day);
    recordsState.batchFilter = taskId;
    recordsState.batchLabel = batchLabel;
    if (taskId) {
      $('#batch-scope').classList.remove('hidden');
      $('#batch-scope span').textContent = batchLabel;
    }
    records.loadRecords().catch(error => ui.toast(error.message, 'danger'));
  }

  function invalidateResultViews() {
    if (state.store) {
      state.store.patch('progress', slice => ({...slice, progressRecordCache: null}));
      state.store.patch('records', slice => ({...slice, recordsStale: true}));
      state.store.patch('report', slice => ({...slice, reportStale: true}));
      state.store.patch('results', slice => ({...slice, resultRevision: (slice.resultRevision || 0) + 1}));
      return;
    }
    progressState.progressRecordCache = null;
    recordsState.recordsStale = reportState.reportStale = true;
    resultsState.resultRevision = (resultsState.resultRevision || 0) + 1;
  }
  async function refreshVisibleResults() {
    if (document.hidden) return;
    if (document.body.dataset.activePage === 'records') {
      await records.loadRecords({background: true});
      await progress.loadDailyResults();
    }
    else if (document.body.dataset.activePage === 'quality') await report.loadReport();
  }

  async function runPoll() {
    if (document.hidden) return;
    try {
      const page = document.body.dataset.activePage;
      if (['progress', 'records'].includes(page) && Date.now() - (progressState.dailyAttemptAt || 0) >= 2000) await progress.loadDailyResults({refreshRecords:false});
      if (page === 'records' && (recordsState.recordsStale || Date.now() - (recordsState.recordAttemptAt || 0) >= 10000)) await records.loadRecords({background:!!recordsState.recordsLoaded});
      else if (page === 'quality' && Date.now() - (reportState.reportAttemptAt || 0) >= 30000) await report.loadReport();
    } catch (_) {
      if (document.body.dataset.activePage === 'progress') $('#progress-note').textContent = '进度连接暂时中断，恢复连接后会重新查询。';
    }
  }
  const polling = createPolling({
    setTimeout,
    clearTimeout,
    run: runPoll,
    getDelay: () => document.hidden ? 5000 : 2000
  });
  async function pollQueue() {
    // Keep explicit calls useful in tests and for a manual refresh before the
    // shell has finished initialization; normal lifecycle polling is started
    // by initialize().
    if (!polling.active) polling.start(0);
    return polling.tick(true);
  }

  let initialized = false;
  function initialize() {
    if (initialized) return;
    initialized = true;
    process_history.initialize();
    result_preview.initialize();
    retention_settings.initialize();
    remote_settings.initialize();
    records.initialize();
    recognition_plan.initialize();
    imports.initialize();
    review.initialize();
    review_queue.initialize();
    progress.initialize();
    report.initialize();
    seal_center.initialize();
    navigation.initialize();
    document.addEventListener('visibilitychange', () => { if (!document.hidden) pollQueue(); });

    window.addEventListener('beforeunload', event => { if (importsState.batchRunning || importsState.importScanning || reviewState.reviewDirty || reviewState.reviewSaving) { event.preventDefault(); event.returnValue=''; } });
    navigation.routePage();
    polling.start(2000);

  }

  return {initialize, state, pollQueue, polling, invalidateResultViews, refreshVisibleResults,
    records, imports, recognition_plan, review, review_evidence, review_queue, progress, report, navigation, process_history,
    retention_settings, seal_center};
}

export function browserEnvironment(window, libraries) {
  return {window, document: window.document, location: window.location, history: window.history,
    localStorage: {
      getItem: key => window.localStorage.getItem(key),
      setItem: (key, value) => window.localStorage.setItem(key, value),
      removeItem: key => window.localStorage.removeItem(key),
    },
    FormData: window.FormData, URLSearchParams: window.URLSearchParams,
    AbortController: window.AbortController, fetch: window.fetch.bind(window),
    setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window),
    ...libraries};
}
