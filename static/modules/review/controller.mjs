import {createReviewView} from './view.mjs';
import {createApiClients} from '../core/api.mjs';
import {localToday, statusPill, escapeHtml} from '../core/ui.mjs';
import {createReviewImage} from './review_image.mjs';
import {createReviewPreview} from './review_preview.mjs';
import {reviewReadiness, reviewKeyboardAction} from './review_readiness.mjs';
import {providerErrorIssues} from '../core/provider_errors.mjs';
import {createReviewEditors} from './editors.mjs';
import {createReviewSubmission} from './submission.mjs';

const optionalStageReasons = new Set([
  '部分识别：未执行项目不能据此判定整单通过',
  '本次为部分识别，未执行项目不参与整单通过判定',
]);

export function createReview({
  environment, ui, navigationState, reviewState, api, services = createApiClients(api), ReceiptWorkbench, fieldSchema, fieldEditor,
  renderProductTable, renderArtifacts, renderHistory, renderGroundTruth, loadReviewQueue,
  continueReview, refreshVisibleResults, retryOne, showPage, routePage,
  startReviewScope, markReviewSaved, getReviewScope, ensureReviewScope
}) {
  services = services && typeof services === 'object' ? services : createApiClients(api);
  const {document, window, location, history} = environment;
  const {$, $$, toast} = ui;
  const reviewImage = createReviewImage({environment, ui, reviewState});
  const reviewPreview = createReviewPreview({environment, ui});

  const {submitReview} = createReviewSubmission({
    ui, reviewState, services, currentReadiness, syncReviewReadiness,
    markReviewSaved, continueReview, refreshVisibleResults
  });

  function currentReadiness() {
    const item = reviewState.current || {}, content = $('#review-content');
    const value = name => $(`[name="${name}"]`, content)?.value || '';
    const seal = value('seal_confirmed_match');
    return reviewReadiness(item, {
      requiredDate: value('field:要求到货'), actualDate: value('actual_date'),
      dateConfirmed: value('actual_date_confirmed') === 'true', sealText: value('seal_text'),
      sealRequirement: value('field:签章要求'), sealConfirmed: seal === '' ? null : seal === 'true',
    });
  }

  function syncReviewReadiness() {
    if (!reviewState.current) return;
    const content = $('#review-content'), readiness = currentReadiness();
    const status = $('[data-review-readiness]', content);
    if (status) {
      status.textContent = reviewState.reviewSaving ? '正在保存，请稍候…' : readiness.message;
      status.dataset.tone = reviewState.reviewSaving ? 'saving' : readiness.ready ? 'ready' : 'warning';
    }
    const pass = $('[data-confirm-pass]', content);
    pass.disabled = reviewState.reviewSaving || !readiness.ready;
    pass.title = readiness.ready ? '确认本单通过并继续下一张（⌘ / Ctrl + Enter）' : readiness.message;
    for (const key of ['date', 'seal']) {
      const ready = readiness[`${key}Ready`];
      $(`[data-check-card="${key}"]`, content).classList.toggle('check-attention', !ready);
      $(`[data-check-badge="${key}"]`, content).innerHTML = statusPill(readiness[`${key}Status`]);
      const summary = $(`[data-check-summary="${key}"]`, content);
      if (summary) {
        const value = ready ? ($(`[name="${key === 'date' ? 'actual_date' : 'seal_text'}"]`, content)?.value || '').trim() : '';
        summary.textContent = value;
        summary.title = value;
        summary.hidden = !value;
      }
    }
    const failures = providerErrorIssues(reviewState.current);
    const issues = [...failures, ...readiness.issues, ...ReceiptWorkbench.issues(reviewState.current).filter(issue =>
      !optionalStageReasons.has(String(issue.message).trim())
      && !['date', 'seal'].includes(issue.target) && !failures.some(error => issue.message.includes(error.message)))];
    $('[data-status-summary]', content).innerHTML = `<div class="review-issue-heading"><strong>${issues.length ? `${issues.length} 项待核对` : '核验信息已齐全'}</strong></div>${issues.length ? `<div class="review-issue-list">${issues.map(issue => `<button type="button" data-issue-target="${issue.target}" aria-pressed="false" title="${escapeHtml(issue.message)}"><span>${escapeHtml(issue.message)}</span><span aria-hidden="true">›</span></button>`).join('')}</div>` : ''}`;
  }

  function canLeaveReview({discard = true} = {}) {
    if (reviewState.reviewSaving) { toast('正在保存，请稍候'); return false; }
    if (reviewState.reviewDirty && !window.confirm('当前修改尚未保存，离开会丢弃修改。是否继续？')) return false;
    if (discard) reviewState.reviewDirty = false;
    return true;
  }
  function setReviewDirty() {
    reviewState.reviewDirty = true;
    reviewState.reviewEditVersion++;
    $('[data-save-state]').textContent = '有未保存的修改';
    $('[data-save-state]').classList.add('unsaved');
    syncReviewReadiness();
  }
  const {setupDateConfirmation, setupSealConfirmation, setupSignatureConfirmation} = createReviewEditors({
    ReceiptWorkbench, $, $$, statusPill, escapeHtml, setReviewDirty
  });
  const renderReview = createReviewView({
    ui, reviewState, ReceiptWorkbench, fieldSchema, fieldEditor, renderProductTable,
    setupDateConfirmation, setupSealConfirmation, setupSignatureConfirmation, applyReviewMode,
    focusReviewImage, reviewImage, reviewPreview, setReviewImage, focusReviewIssue,
    renderArtifacts, canLeaveReview, retryOne, submitReview, setReviewDirty, syncReviewReadiness
  });
  function showReviewEmpty({loading = false, error = ''} = {}) {
    reviewPreview.cancel();
    reviewState.current = null;
    reviewState.reviewDirty = false;
    $('#review-modal').classList.add('hidden');
    $('#review-empty').classList.remove('hidden');
    const deferred = reviewState.reviewDeferred.size;
    $('#review-empty').classList.toggle('is-loading', loading);
    $('#review-empty').classList.toggle('has-error', !!error);
    $('#review-empty').setAttribute('aria-busy', String(loading));
    $('#review-empty h2').textContent = loading ? '正在加载回单…' : error ? '回单暂时无法加载' : deferred ? '本轮复核已结束' : '当前范围没有待复核回单';
    $('#review-empty p').textContent = loading ? '正在准备当前范围的复核内容。' : error || (deferred ? `${deferred} 张已保存，留待稍后处理。` : '可以关闭窗口返回回单记录，选择其他回单继续复核。');
    $('#review-revisit').classList.toggle('hidden', loading || !!error || !deferred);
  }
  function setReviewImage(index, note = '', options = {}) {
    const source = reviewState.reviewImages?.[index], content = $('#review-content');
    reviewState.reviewImageIndex = index; reviewState.reviewZoom = 1;
    $$('[data-image-source-group]', content).forEach(button => {
      const group = button.dataset.imageSourceGroup;
      const available = reviewState.reviewImages?.some(image => image.group === group);
      button.disabled = !available;
      button.setAttribute('aria-pressed', String(!!source && group === source.group));
    });
    $$('[data-focus-image]', content).forEach(button => button.setAttribute('aria-pressed', String(button.dataset.focusImage === source?.group)));
    reviewPreview.show(source, {
      caption: note || (source?.group === 'page' ? '整页回单 · 拖动查看，按住 ⌘ / Ctrl 滚轮缩放' : source ? `${source.label} · 原色裁剪图，可拖动与缩放` : '当前记录没有保存可用的预览图片'),
      alt: source ? `${reviewState.current.filename} · ${source.label}` : '',
      canFallback: source?.group !== 'page' && reviewState.reviewImages.some(image => image.group === 'page'),
      retry: options.retry,
    });
    reviewImage.reset();
  }
  function focusReviewImage(group) {
    const index = reviewState.reviewImages.findIndex(source => source.group === group);
    if (index >= 0) setReviewImage(index);
    else setReviewImage(0, `没有保存${group === 'date' ? '日期' : '印章'}区域图，请在整页回单中核对。`);
  }
  function focusReviewIssue(target) {
    const content = $('#review-content');
    if (target === 'date' || target === 'seal') focusReviewImage(target);
    const selector = ['date','seal'].includes(target) ? `[data-check-card="${target}"]` : `[data-review-panel="${target}"]`;
    const element = $(selector, content);
    for (let parent = element; parent && parent !== content; parent = parent.parentElement) { if (parent.tagName === 'DETAILS') parent.open = true; }
    $$('[data-review-panel],[data-check-card]', content).forEach(panel => panel.classList.remove('review-focus-target'));
    $$('[data-issue-target]', content).forEach(button => {
      button.classList.toggle('is-active', button.dataset.issueTarget === target);
      button.setAttribute('aria-pressed', String(button.dataset.issueTarget === target));
    });
    if (element) {
      element.classList.add('review-focus-target');
      element.scrollIntoView({
        block: 'center',
        inline: 'nearest',
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'auto' : 'smooth'
      });
      if (element.matches('input,textarea')) element.focus({preventScroll:true});
    }
  }
  async function openReview(id, {mode = 'review'} = {}) {
    mode = mode === 'view' ? 'view' : 'review';
    if (!canLeaveReview({discard:false})) {
      if (reviewState.current && document.body.dataset.activePage === 'review') history.replaceState(null, '', `#review/${reviewState.current.id}`);
      return;
    }
    reviewState.reviewIntent = (reviewState.reviewIntent || 0) + 1;
    const editVersion = reviewState.reviewEditVersion;
    if (document.body.dataset.activePage !== 'review') {
      reviewState.reviewOrigin = {hash: '#records', scroll: location.hash === '#records' ? window.scrollY : 0};
    }
    const originHash = location.hash;
    const requestId = reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
    const item = await (services.records.get(id));
    if (requestId !== reviewState.reviewRequest || location.hash !== originHash) return;
    if (reviewState.reviewSaving || reviewState.reviewEditVersion !== editVersion) {
      if (reviewState.current) history.replaceState(null, '', `#review/${reviewState.current.id}`);
      toast('加载期间有新的修改，已保留当前回单，请保存后再切换。', 'warning');
      return;
    }
    ensureReviewScope(item);
    reviewState.current = item; reviewState.reviewDirty = false; reviewState.reviewMode = mode;
    const replaceFocusedForm = $('#review-content').contains?.(document.activeElement);
    renderReview(item, id, mode);
    // Switching receipts stays inside a single modal history entry.
    const route = `${mode}/${id}`;
    if (document.body.dataset.activePage === 'review' || /^#(review|view)\//.test(location.hash)) history.replaceState(null, '', `#${route}`);
    else history.pushState(null, '', `#${route}`);
    showPage('review');
    if (replaceFocusedForm) {
      $('#review-title').setAttribute('tabindex', '-1');
      $('#review-title').focus({preventScroll: true});
    }
    await Promise.allSettled([renderHistory(), renderGroundTruth(), loadReviewQueue(false)]);
  }

  function applyReviewMode(mode, id, content) {
    const view = mode === 'view';
    content.dataset.reviewMode = mode;
    const note = $('[data-review-mode-note]', content);
    if (note) note.textContent = view ? '只读查看模式 · 如需修改或提交，请进入复核' : '';
    $('[data-review-readiness]', content).classList.toggle('hidden', view);
    $('[data-review-shortcuts]', content).classList.toggle('hidden', view);
    $('[data-enter-review]', content).classList.toggle('hidden', !view);
    for (const selector of ['[data-retry]', '[data-save-pending]', '[data-confirm-fail]', '[data-confirm-pass]']) {
      $(selector, content).classList.toggle('hidden', view);
    }
    if (!view) return;
    for (const element of $$('input, textarea, select', content)) element.disabled = true;
    for (const selector of [
      '[data-date-choice]', '[data-date-accept]', '[data-date-change]',
      '[data-seal-choice]', '[data-seal-change]',
      '[data-signature-choice]', '[data-signature-change]'
    ]) $(selector, content)?.setAttribute('disabled', 'disabled');
    $('[data-enter-review]', content).addEventListener('click', () => openReview(id, {mode: 'review'}));
  }

  function dismissReview() {
    reviewPreview.cancel();
    reviewState.current = null;
    reviewState.reviewDirty = false;
    reviewState.reviewIntent = (reviewState.reviewIntent || 0) + 1;
    reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
    reviewState.queueRequest = (reviewState.queueRequest || 0) + 1;
    reviewState.reviewQueueController?.abort();
  }
  function closeModal() {
    if (!canLeaveReview()) return;
    dismissReview();
    history.replaceState(null, '', '#records');
    navigationState.restoreScroll = reviewState.reviewOrigin?.scroll || 0;
    routePage();
  }

  async function openBatchReview() {
    await startReviewScope(getReviewScope());
  }

  function initialize() {
    $$('[data-close-modal]').forEach(node => node.addEventListener('click', closeModal));
    $('#review-dialog')?.addEventListener('cancel', event => {
      event.preventDefault();
      closeModal();
    });
    document.addEventListener('keydown', event => {
      if (document.body.dataset.activePage !== 'review' || !reviewState.current) return;
      const action = reviewKeyboardAction(event);
      if (!action) return;
      event.preventDefault();
      if (reviewState.reviewSaving) return;
      if (reviewState.reviewMode === 'view' && ['save', 'pass'].includes(action)) {
        toast('当前为只读查看模式，请先进入复核。', 'warning');
        return;
      }
      if (action === 'save') submitReview('待复核', '需人工复核');
      else if (action === 'pass') {
        if (currentReadiness().ready) submitReview('确认通过', '通过');
        else toast(currentReadiness().message, 'warning');
      } else if (action === 'date' || action === 'seal') focusReviewIssue(action);
      else reviewImage.zoom(action === 'zoom-in' ? 'in' : 'out');
    });

    $('#open-batch-review').addEventListener('click', openBatchReview);

    reviewState.reviewDay ??= localToday();

  }

  return {initialize, canLeaveReview, setReviewDirty, syncReviewReadiness, showReviewEmpty, setReviewImage, focusReviewImage, focusReviewIssue, openReview, setupDateConfirmation, setupSealConfirmation, submitReview, dismissReview, closeModal, openBatchReview};
}
