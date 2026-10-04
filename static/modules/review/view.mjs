/** Review form rendering and form-local event binding. */
import {filenameParts, statusPill, escapeHtml} from '../core/ui.mjs';
import {dateAuditText, sealEvidenceMarkup} from './review_provider_evidence.mjs';

export function createReviewView({ui, reviewState, ReceiptWorkbench, fieldSchema, fieldEditor,
  renderProductTable, setupDateConfirmation, setupSealConfirmation, setupSignatureConfirmation,
  applyReviewMode, focusReviewImage, reviewImage, reviewPreview, setReviewImage, focusReviewIssue,
  renderArtifacts, canLeaveReview, retryOne, submitReview, setReviewDirty, syncReviewReadiness}) {
  const {$, $$} = ui;
  return function renderReview(item, id, mode) {
    $('#review-empty').classList.add('hidden'); $('#review-modal').classList.remove('hidden');
    $('#review-dialog').dataset.reviewMode = mode;
    $('#review-dialog-title').textContent = mode === 'view' ? '查看回单' : '人工复核';
    $('#toggle-review-queue').classList.toggle('hidden', mode === 'view');
    $('#review-session-progress').classList.toggle('hidden', mode === 'view');
    $('.review-workspace').classList.toggle('view-mode', mode === 'view');
    $('#review-title').textContent = filenameParts(item.filename).name;
    $('#review-title').title = item.filename;
    $('#review-subtitle').textContent = [item.fields?.['客户名称'], item.document_type?.label, item.page_group?.page_count > 1 ? `共 ${item.page_group.page_count} 页` : ''].filter(Boolean).join(' · ');
    $('[data-save-state]').textContent = ''; $('[data-save-state]').classList.remove('unsaved');
    const content = $('#review-content');
    content.replaceChildren($('#review-template').content.cloneNode(true));
    $('[name=result_id]', content).value = id;
    const issues = ReceiptWorkbench.issues(item);
    const renderFields = (selector, names) => { $(selector, content).innerHTML = names.map(name => fieldEditor(name, item.fields?.[name] || '', item.field_metadata?.[name])).join(''); };
    renderFields('[data-fields]', fieldSchema.printed);
    renderFields('[data-handwritten-fields]', fieldSchema.handwritten);
    renderProductTable(item.product_table, content);
    $('[data-product-count]', content).textContent = item.product_table?.status === '未执行' ? '未开启识别' : `${item.product_table?.rows?.length || 0} 行`;
    $('[data-date-comparison]', content).innerHTML = `<span>要求到货 <b>${escapeHtml(item.fields?.['要求到货'] || item.date_check?.required || '未提供')}</b></span>`;
    $('[data-date-result]', content).innerHTML = `<span>识别到的签收日期</span><b>${escapeHtml(item.date_check?.actual_display || item.date_check?.actual || '未识别')}</b>`;
    $('[data-seal-comparison]', content).innerHTML = `<span>签章要求 <b>${escapeHtml(item.fields?.['签章要求'] || '未提供')}</b></span>`;
    $('[data-seal-result]', content).textContent = item.seal_check?.message || '';
    for (const key of ['date', 'seal', 'signature']) {
      const status = ReceiptWorkbench.checkStatus(item, key);
      const card = $(`[data-check-card="${key}"]`, content);
      const blocking = ['date', 'seal'].includes(key) && status !== '匹配';
      card.open = blocking;
      card.classList.toggle('check-attention', blocking);
      $(`[data-check-badge="${key}"]`, content).innerHTML = statusPill(status);
    }
    setupDateConfirmation(item, content);
    const sealEvidence = sealEvidenceMarkup(item);
    if (sealEvidence) {
      const target = $('[data-seal-dual]', content);
      target.classList.remove('hidden');
      target.innerHTML = sealEvidence;
    }
    $('[data-date-audit-content]', content).textContent = dateAuditText(item);
    setupSealConfirmation(item, content);
    setupSignatureConfirmation(item, content);
    $('[name=human_note]', content).value = item.human_note || '';
    $('[name=error_type]', content).value = item.error_type || '';
    applyReviewMode(mode, id, content);
    reviewState.reviewImages = ReceiptWorkbench.imageSources(item);
    $$('[data-image-source-group]', content).forEach(button => {
      button.addEventListener('click', () => focusReviewImage(button.dataset.imageSourceGroup));
    });
    reviewImage.attach(content);
    reviewPreview.attach(content, {
      retry: () => setReviewImage(reviewState.reviewImageIndex, '', {retry: true}),
      fallback: () => focusReviewImage('page'),
    });
    setReviewImage(0);
    const firstImageIssue = issues.find(issue => ['date','seal'].includes(issue.target));
    if (firstImageIssue) focusReviewImage(firstImageIssue.target);
    $$('[data-focus-image]', content).forEach(button => button.addEventListener('click', () => focusReviewImage(button.dataset.focusImage)));
    $('[data-status-summary]', content).addEventListener('click', event => {
      const target = event.target.closest('[data-issue-target]');
      if (target) focusReviewIssue(target.dataset.issueTarget);
    });
    reviewState.artifactTab = firstImageIssue?.target === 'seal' ? 'seals' : 'date';
    $$('[data-artifact-tab]', content).forEach(button => { button.classList.toggle('active', button.dataset.artifactTab === reviewState.artifactTab); button.addEventListener('click', () => { reviewState.artifactTab = button.dataset.artifactTab; $$('[data-artifact-tab]', content).forEach(x => x.classList.toggle('active', x === button)); renderArtifacts(); }); });
    renderArtifacts();
    $('[data-retry]', content).addEventListener('click', () => { if (canLeaveReview()) retryOne(id); });
    $('[data-save-pending]', content).addEventListener('click', () => submitReview('待复核', '需人工复核'));
    $('[data-confirm-pass]', content).addEventListener('click', () => submitReview('确认通过', '通过'));
    $('[data-confirm-fail]', content).addEventListener('click', () => submitReview('确认不通过', '不通过'));
    $('#review-form', content).addEventListener('input', setReviewDirty);
    $('#review-form', content).addEventListener('change', setReviewDirty);
    $('#review-form', content).addEventListener('submit', event => event.preventDefault());
    syncReviewReadiness();
  };
}
