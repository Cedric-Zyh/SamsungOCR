import {createState} from './state.mjs';
import {createUi} from './ui.mjs';
import {createApi} from './api.mjs';
import {createRecords} from './records.mjs?v=20260916-process-history';
import {createImports} from './imports.mjs';
import {createRecognitionPlan} from './recognition_plan.mjs';
import {createReview} from './review.mjs';
import {createReviewEvidence} from './review_evidence.mjs';
import {createReviewQueue} from './review_queue.mjs';
import {createProgress} from './progress.mjs?v=20260916-process-history';
import {createReport} from './report.mjs';
import {createNavigation} from './navigation.mjs';
import {createProcessHistory} from './process_history.mjs?v=20260916-process-history';

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
  const {ReceiptImport, ReceiptQueue, ReceiptWorkbench} = environment;
  const api = options.api || createApi({fetch: environment.fetch, onResultsChanged: invalidateResultViews});
  const process_history = createProcessHistory({
    environment,
    ui,
    api
  });
  const records = createRecords({
    ReceiptWorkbench,
    environment,
    ui,
    recordsState,
    reviewState,
    api,
    startReviewScope: (...args) => review_queue.startReviewScope(...args),
    retryOne: (...args) => imports.retryOne(...args),
    openProcessHistory: (...args) => process_history.open(...args),
    refreshVisibleResults,
    setBatchStep: (...args) => imports.setBatchStep(...args)
  });
  const imports = createImports({
    environment,
    ui,
    importsState,
    recordsState,
    progressState,
    api,
    ReceiptImport,
    readRecognitionPlan: (...args) => recognition_plan.readRecognitionPlan(...args),
    readAcceptancePolicy: (...args) => recognition_plan.readAcceptancePolicy(...args),
    usesRemotePlan: (...args) => recognition_plan.usesRemotePlan(...args),
    updateRecognitionPlan: (...args) => recognition_plan.updateRecognitionPlan(...args),
    loadDailyResults: (...args) => progress.loadDailyResults(...args),
    syncStartRecognition: (...args) => progress.syncStartRecognition?.(...args),
    showQueuedRetry: (...args) => progress.showQueuedRetry(...args),
    closeModal: (...args) => review.closeModal(...args)
  });
  const recognition_plan = createRecognitionPlan({
    environment,
    ui,
    importsState
  });
  const review = createReview({
    environment,
    ui,
    navigationState,
    reviewState,
    api,
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
    fieldSchema
  });
  const review_queue = createReviewQueue({
    environment,
    ui,
    reviewState,
    api,
    ReceiptWorkbench,
    canLeaveReview: (...args) => review.canLeaveReview(...args),
    openReview: (...args) => review.openReview(...args),
    showReviewEmpty: (...args) => review.showReviewEmpty(...args),
    showPage: (...args) => navigation.showPage(...args)
  });
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
    ReceiptWorkbench,
    ReceiptQueue,
    retryOne: (...args) => imports.retryOne(...args),
    startReviewScope: (...args) => review_queue.startReviewScope(...args),
    openProcessHistory: (...args) => process_history.open(...args)
  });
  const report = createReport({
    environment,
    ui,
    reportState,
    resultsState,
    api
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
    loadReport: (...args) => report.loadReport(...args)
  });

  function invalidateResultViews() {
    progressState.progressRecordCache = null;
    recordsState.recordsStale = reportState.reportStale = true;
    resultsState.resultRevision = (resultsState.resultRevision || 0) + 1;
  }
  async function refreshVisibleResults() {
    if (document.hidden) return;
    if (document.body.dataset.activePage === 'records') await records.loadRecords({background: true});
    else if (document.body.dataset.activePage === 'quality') await report.loadReport();
  }

  async function pollQueue() {
    clearTimeout(navigationState.pollTimer);
    if (navigationState.pollRunning) return;
    navigationState.pollRunning = true;
    try {
      if (!document.hidden) {
        const page = document.body.dataset.activePage;
        if (page === 'progress' && Date.now() - (progressState.dailyAttemptAt || 0) >= 2000) await progress.loadDailyResults({refreshRecords:false});
        else if (page === 'records' && Date.now() - (recordsState.recordAttemptAt || 0) >= 10000) await records.loadRecords({background:!!recordsState.recordsLoaded});
        else if (page === 'quality' && Date.now() - (reportState.reportAttemptAt || 0) >= 30000) await report.loadReport();
      }
    } catch (_) {
      if (document.body.dataset.activePage === 'progress') $('#progress-note').textContent = '进度连接暂时中断，恢复连接后会重新查询。';
    } finally {
      navigationState.pollRunning = false;
      navigationState.pollTimer = setTimeout(pollQueue, document.hidden ? 5000 : 2000);
    }
  }

  let initialized = false;
  function initialize() {
    if (initialized) return;
    initialized = true;
    process_history.initialize();
    records.initialize();
    recognition_plan.initialize();
    imports.initialize();
    review.initialize();
    review_queue.initialize();
    progress.initialize();
    report.initialize();
    navigation.initialize();
    $('#workbench-open-records')?.addEventListener('click', () => records.openDayRecords($('#progress-date').value));
    document.addEventListener('visibilitychange', () => { if (!document.hidden) pollQueue(); });

    window.addEventListener('beforeunload', event => { if (importsState.batchRunning || importsState.importScanning || reviewState.reviewDirty || reviewState.reviewSaving) { event.preventDefault(); event.returnValue=''; } });
    navigation.routePage();
    navigationState.pollTimer = setTimeout(pollQueue, 2000);

  }

  return {initialize, state, pollQueue, invalidateResultViews, refreshVisibleResults,
    records, imports, recognition_plan, review, review_evidence, review_queue, progress, report, navigation, process_history};
}

export function browserEnvironment(window, libraries) {
  return {window, document: window.document, location: window.location, history: window.history,
    localStorage: {getItem: key => window.localStorage.getItem(key), setItem: (key, value) => window.localStorage.setItem(key, value)},
    FormData: window.FormData, URLSearchParams: window.URLSearchParams,
    AbortController: window.AbortController, fetch: window.fetch.bind(window),
    setTimeout: window.setTimeout.bind(window), clearTimeout: window.clearTimeout.bind(window),
    ...libraries};
}
