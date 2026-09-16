import {localToday, filenameParts, statusPill, escapeHtml, planLabels} from './ui.mjs';
import {createReviewImage} from './review_image.mjs';
import {createReviewPreview} from './review_preview.mjs';
import {reviewReadiness, reviewKeyboardAction} from './review_readiness.mjs';
import {dateAuditText, sealEvidenceMarkup} from './review_provider_evidence.mjs';
import {providerErrorIssues} from './provider_errors.mjs';

const optionalStageReasons = new Set([
  '部分识别：未执行项目不能据此判定整单通过',
  '本次为部分识别，未执行项目不参与整单通过判定',
]);

export function createReview({
  environment, ui, navigationState, reviewState, api, ReceiptWorkbench, fieldSchema, fieldEditor,
  renderProductTable, renderArtifacts, renderHistory, renderGroundTruth, loadReviewQueue,
  continueReview, refreshVisibleResults, retryOne, showPage, routePage,
  startReviewScope, markReviewSaved, getReviewScope, ensureReviewScope
}) {
  const {document, window, location, history} = environment;
  const {$, $$, toast} = ui;
  const reviewImage = createReviewImage({environment, ui, reviewState});
  const reviewPreview = createReviewPreview({environment, ui});

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
    $('[data-status-summary]', content).innerHTML = `<div class="review-issue-heading"><strong>${issues.length ? `${issues.length} 项待核对` : '核验信息已齐全'}</strong></div>${issues.length ? `<div class="review-issue-list">${issues.map(issue => `<button type="button" data-issue-target="${issue.target}" title="${escapeHtml(issue.message)}"><span>${escapeHtml(issue.message)}</span><span aria-hidden="true">›</span></button>`).join('')}</div>` : ''}`;
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
    if (source) $('[data-image-source]', content).value = String(index);
    $('[data-image-source]', content).disabled = !source;
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
    if (element) { element.scrollIntoView({block:'nearest'}); if (element.matches('input,textarea')) element.focus({preventScroll:true}); }
  }
  async function openReview(id) {
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
    const item = await api(`/api/results/${id}`);
    if (requestId !== reviewState.reviewRequest || location.hash !== originHash) return;
    if (reviewState.reviewSaving || reviewState.reviewEditVersion !== editVersion) {
      if (reviewState.current) history.replaceState(null, '', `#review/${reviewState.current.id}`);
      toast('加载期间有新的修改，已保留当前回单，请保存后再切换。', 'warning');
      return;
    }
    ensureReviewScope(item);
    reviewState.current = item; reviewState.reviewDirty = false;
    $('#review-empty').classList.add('hidden'); $('#review-modal').classList.remove('hidden');
    $('#review-title').textContent = filenameParts(item.filename).name;
    $('#review-title').title = item.filename;
    $('#review-subtitle').textContent = [item.fields?.['客户名称'], item.document_type?.label, item.page_group?.page_count > 1 ? `共 ${item.page_group.page_count} 页` : ''].filter(Boolean).join(' · ');
    $('[data-save-state]').textContent = ''; $('[data-save-state]').classList.remove('unsaved');
    const content = $('#review-content');
    const replaceFocusedForm = content.contains?.(document.activeElement);
    content.replaceChildren($('#review-template').content.cloneNode(true));
    $('[name=result_id]', content).value = id;
    const issues = ReceiptWorkbench.issues(item);
    $('[data-machine-summary]', content).innerHTML = `<p>${escapeHtml(item.safety_policy || '')}</p><p>${escapeHtml((item.review_reasons || []).join('；'))}</p><p>${escapeHtml(Object.entries(item.recognition_status || {}).map(([k,v]) => `${planLabels[k]}：${v}`).join(' · '))}</p>`;
    const renderFields = (selector, names) => { $(selector, content).innerHTML = names.map(name => fieldEditor(name, item.fields?.[name] || '', item.field_metadata?.[name])).join(''); };
    renderFields('[data-fields]', fieldSchema.printed);
    renderFields('[data-handwritten-fields]', fieldSchema.handwritten);
    renderProductTable(item.product_table, content);
    $('[data-product-count]', content).textContent = item.product_table?.status === '未执行' ? '未开启识别' : `${item.product_table?.rows?.length || 0} 行`;
    $('[data-date-comparison]', content).innerHTML = `<span>要求到货 <b>${escapeHtml(item.fields?.['要求到货'] || item.date_check?.required || '未提供')}</b></span>`;
    $('[data-date-result]', content).innerHTML = `<span>识别到的签收日期</span><b>${escapeHtml(item.date_check?.actual || '未识别')}</b>`;
    $('[data-seal-comparison]', content).innerHTML = `<span>签章要求 <b>${escapeHtml(item.fields?.['签章要求'] || '未提供')}</b></span>`;
    $('[data-seal-result]', content).textContent = item.seal_check?.message || '';
    for (const key of ['date', 'seal', 'signature']) {
      const status = ReceiptWorkbench.checkStatus(item, key);
      const card = $(`[data-check-card="${key}"]`, content);
      card.open = status !== '匹配';
      card.classList.toggle('check-attention', status !== '匹配');
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
    reviewState.reviewImages = ReceiptWorkbench.imageSources(item);
    $('[data-image-source]', content).innerHTML = reviewState.reviewImages.map((image,index) => `<option value="${index}">${escapeHtml(image.label)}</option>`).join('') || '<option>暂无图片</option>';
    $('[data-image-source]', content).addEventListener('change', event => setReviewImage(Number(event.target.value)));
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
    $('[data-variants]', content).innerHTML = renderRecognitionVariants(item);
    $('[data-retry]', content).addEventListener('click', () => { if (canLeaveReview()) retryOne(id); });
    $('[data-save-pending]', content).addEventListener('click', () => submitReview('待复核', '需人工复核'));
    $('[data-confirm-pass]', content).addEventListener('click', () => submitReview('确认通过', '通过'));
    $('[data-confirm-fail]', content).addEventListener('click', () => submitReview('确认不通过', '不通过'));
    $('#review-form', content).addEventListener('input', setReviewDirty);
    $('#review-form', content).addEventListener('change', setReviewDirty);
    $('#review-form', content).addEventListener('submit', event => event.preventDefault());
    syncReviewReadiness();
    // Switching receipts stays inside a single modal history entry.
    if (document.body.dataset.activePage === 'review' || /^#review/.test(location.hash)) history.replaceState(null, '', `#review/${id}`);
    else history.pushState(null, '', `#review/${id}`);
    showPage('review');
    if (replaceFocusedForm) {
      $('#review-title').setAttribute('tabindex', '-1');
      $('#review-title').focus({preventScroll: true});
    }
    await Promise.allSettled([renderHistory(), renderGroundTruth(), loadReviewQueue(false)]);
  }

  function setupDateConfirmation(item, content) {
    let model = ReceiptWorkbench.dateReview(item.fields?.['要求到货'] || item.date_check?.required, item.date_check?.actual);
    let confirmed = !!model.actual && item.date_check?.source === '人工复核';
    let autoConfirmed = !confirmed && !!model.actual && item.date_check?.status === '匹配' && item.date_check?.reliable === true;
    let edited = false;
    const actual = $('[name=actual_date]', content), editor = $('[name=date_correction]', content);
    const same = $('[data-date-choice=same]', content), different = $('[data-date-choice=different]', content);
    function renderValue() {
      actual.value = model.actual;
      $('[name=actual_date_confirmed]', content).value = String(confirmed || autoConfirmed);
      $('[data-date-accept]', content).disabled = !model.actual;
      $('[data-confirmed-date-value]', content).textContent = model.actual;
      $('[data-date-save-note]', content).textContent = edited ? '待保存' : '已保存';
      $('[data-date-confirmed]', content).classList.toggle('hidden', !(confirmed || autoConfirmed));
      $('[data-date-result]', content).classList.toggle('hidden', confirmed || autoConfirmed);
      $('.date-confirmation', content).classList.toggle('hidden', confirmed || autoConfirmed);
      $('[data-check-badge=date]', content).innerHTML = statusPill(confirmed && edited ? '待保存' : ReceiptWorkbench.checkStatus(item, 'date'));
    }
    function render() {
      editor.value = model.draft;
      $('[data-date-question]', content).textContent = model.required ? '与要求到货同一天？' : '请核对图片填写签收日期';
      same.disabled = !model.required;
      different.classList.toggle('hidden', !model.required);
      same.classList.toggle('hidden', !model.required);
      same.setAttribute('aria-pressed', String(edited && model.choice === 'same'));
      different.setAttribute('aria-pressed', String(edited && model.choice === 'different'));
      $('[data-date-editor]', content).classList.toggle('hidden', !!model.required && model.choice !== 'different');
      $('[data-date-hint]', content).textContent = model.required ? '与要求同一天请点“同一天”；修改后请点“确认日期”。' : '选择实际日期后点“确认日期”。';
      renderValue();
    }
    same.addEventListener('click', () => { model = ReceiptWorkbench.chooseDate(model, 'same'); confirmed = true; autoConfirmed = false; edited = true; render(); setReviewDirty(); $('[data-date-change]', content).focus(); });
    different.addEventListener('click', () => { model = ReceiptWorkbench.chooseDate(model, 'different'); confirmed = false; autoConfirmed = false; edited = true; render(); setReviewDirty(); editor.focus(); });
    editor.addEventListener('input', () => {
      model = ReceiptWorkbench.chooseDate(model, 'edit', editor.value);
      confirmed = false; autoConfirmed = false; edited = true; renderValue();
    });
    $('[data-date-accept]', content).addEventListener('click', () => {
      if (!model.actual) return;
      confirmed = true; autoConfirmed = false; edited = true; render(); setReviewDirty(); $('[data-date-change]', content).focus();
    });
    $('[data-date-change]', content).addEventListener('click', () => {
      confirmed = false; autoConfirmed = false; edited = true; render(); setReviewDirty();
      (model.choice === 'different' || !model.required ? editor : same).focus();
    });
    $('[name="field:要求到货"]', content)?.addEventListener('input', event => {
      model = ReceiptWorkbench.dateReview(event.target.value, '');
      confirmed = false; autoConfirmed = false; edited = true;
      $('[data-date-comparison]', content).innerHTML = `<span>要求到货 <b>${escapeHtml(event.target.value || '未提供')}</b></span>`;
      render();
    });
    render();
  }

  function setupSealConfirmation(item, content) {
    const input = $('[name=seal_text]', content), decision = $('[name=seal_confirmed_match]', content);
    input.value = item.seal_check?.recognized || '';
    const humanConfirmed = item.seal_check?.human_confirmed_match;
    let autoConfirmed = item.seal_check?.status === '匹配' && item.seal_check?.reliable === true;
    let confirmed = autoConfirmed ? undefined : humanConfirmed;
    let edited = false, editingText = false;
    function render() {
      const hasDecision = typeof confirmed === 'boolean' || autoConfirmed;
      decision.value = typeof confirmed === 'boolean' ? String(confirmed) : '';
      $('[data-seal-value]', content).textContent = input.value.trim() || '未识别';
      $('[data-seal-value-label]', content).textContent = hasDecision ? '客户印章' : '识别印章';
      $('[data-seal-confirmation-status]', content).innerHTML = hasDecision ? statusPill(autoConfirmed || confirmed ? '匹配' : '不匹配') : '';
      $('[data-seal-save-note]', content).textContent = hasDecision ? (edited ? '待保存' : '已保存') : '';
      $('[data-seal-change]', content).textContent = hasDecision ? '修改' : '修改文字';
      $('[data-seal-editor]', content).classList.toggle('hidden', !editingText);
      $('[data-seal-prompt]', content).classList.toggle('hidden', hasDecision);
      $('[data-seal-choice=same]', content).disabled = !input.value.trim() || !$('[name="field:签章要求"]', content)?.value.trim();
      $('[data-check-badge=seal]', content).innerHTML = statusPill(edited ? (hasDecision ? '待保存' : '待确认') : ReceiptWorkbench.checkStatus(item, 'seal'));
    }
    $$('[data-seal-choice]', content).forEach(button => button.addEventListener('click', () => {
      confirmed = button.dataset.sealChoice === 'same'; autoConfirmed = false; edited = true; editingText = false;
      render(); setReviewDirty(); $('[data-seal-change]', content).focus();
    }));
    $('[data-seal-change]', content).addEventListener('click', () => {
      confirmed = undefined; autoConfirmed = false; editingText = true; edited = true;
      render(); setReviewDirty(); input.focus();
    });
    input.addEventListener('input', () => { confirmed = undefined; autoConfirmed = false; edited = true; render(); });
    $('[name="field:签章要求"]', content)?.addEventListener('input', event => {
      confirmed = undefined; autoConfirmed = false; edited = true;
      $('[data-seal-comparison]', content).innerHTML = `<span>签章要求 <b>${escapeHtml(event.target.value || '未提供')}</b></span>`;
      render();
    });
    render();
  }

  function setupSignatureConfirmation(item, content) {
    const check = ReceiptWorkbench.signatureCheck(item);
    const decision = $('[name=signature_confirmed_match]', content);
    let autoConfirmed = check.status === '匹配' && check.reliable === true
      && check.source !== '人工复核';
    let confirmed = autoConfirmed ? undefined : check.human_confirmed_match;
    let edited = false;

    function render() {
      const contact = $('[name="field:仓库联系人"]', content)?.value.trim() || '未识别';
      const receiver = $('[name="field:仓库接收人"]', content)?.value.trim() || '未识别';
      const hasDecision = typeof confirmed === 'boolean' || autoConfirmed;
      decision.value = typeof confirmed === 'boolean' ? String(confirmed) : '';
      $('[data-signature-contact]', content).innerHTML = `<span>仓库联系人</span><b>${escapeHtml(contact)}</b>`;
      $('[data-signature-receiver]', content).innerHTML = `<span>仓库接收人</span><b>${escapeHtml(receiver)}</b>`;
      $('[data-confirmed-signature-status]', content).textContent = hasDecision ? (autoConfirmed || confirmed ? '匹配' : '不匹配') : '';
      $('[data-signature-save-note]', content).textContent = hasDecision ? (edited ? '待保存' : '已保存') : '';
      $('[data-signature-confirmed]', content).classList.toggle('hidden', !hasDecision);
      $('[data-signature-prompt]', content).classList.toggle('hidden', hasDecision);
      $('[data-signature-choice=same]', content).disabled = contact === '未识别' || receiver === '未识别';
      $('[data-check-badge=signature]', content).innerHTML = statusPill(edited ? (hasDecision ? '待保存' : '待确认') : ReceiptWorkbench.checkStatus(item, 'signature'));
    }

    $$('[data-signature-choice]', content).forEach(button => button.addEventListener('click', () => {
      confirmed = button.dataset.signatureChoice === 'same';
      autoConfirmed = false; edited = true; render(); setReviewDirty(); $('[data-signature-change]', content).focus();
    }));
    $('[data-signature-change]', content).addEventListener('click', () => {
      confirmed = undefined; autoConfirmed = false; edited = true; render(); setReviewDirty();
    });
    for (const selector of ['[name="field:仓库联系人"]', '[name="field:仓库接收人"]']) {
      $(selector, content)?.addEventListener('input', () => {
        confirmed = undefined; autoConfirmed = false; edited = true; render();
      });
    }
    render();
  }

  async function submitReview(reviewStatus, finalResult) {
    if (reviewState.reviewSaving) return;
    const form = $('#review-form'), fields = {};
    $$('[name^="field:"]', form).forEach(input => fields[input.name.slice(6)] = input.value);
    const productRows = $$('.product-cell-input', form).map(input => ({row: Number(input.dataset.productRow), column: input.dataset.productColumn, value: input.value}));
    const saveGroundTruth = form.save_ground_truth.checked;
    if (saveGroundTruth && reviewStatus === '待复核') return toast('评测真值只能在确认通过或确认不通过时保存', 'warning');
    if (saveGroundTruth && !['true', 'false'].includes(form.truth_seal_should_match.value)) return toast('请人工选择印章真值结论', 'warning');
    if (reviewStatus === '确认通过' && !currentReadiness().ready) {
      return toast(currentReadiness().message, 'warning');
    }
    const truthSealShouldMatch = form.truth_seal_should_match.value === '' ? null : form.truth_seal_should_match.value === 'true';
    const payload = {fields, product_rows: productRows, actual_date: form.actual_date.value, seal_text: form.seal_text.value, error_type: form.error_type.value, human_note: form.human_note.value, review_status: reviewStatus, final_result: finalResult, action: reviewStatus, save_ground_truth: saveGroundTruth, truth_seal_should_match: truthSealShouldMatch};
    payload.review_revision = reviewState.current.review_revision;
    payload.actual_date_confirmed = form.actual_date_confirmed.value === 'true';
    payload.seal_confirmed_match = form.seal_confirmed_match.value === '' ? null : form.seal_confirmed_match.value === 'true';
    payload.signature_confirmed_match = form.signature_confirmed_match
      ? (form.signature_confirmed_match.value === '' ? null : form.signature_confirmed_match.value === 'true')
      : null;
    const id = reviewState.current.id;
    reviewState.reviewRequest = (reviewState.reviewRequest || 0) + 1;
    const controls = $$('button,input,textarea,select', form).map(el => [el, el.disabled]);
    reviewState.reviewSaving = true; controls.forEach(([el]) => el.disabled = true);
    syncReviewReadiness();
    $('[data-save-state]').textContent = '正在保存…';
    let saved = false;
    try {
      reviewState.current = await api(`/api/results/${id}/review`, {method: 'PATCH', json: payload});
      reviewState.reviewDirty = false; saved = true;
      markReviewSaved(id, reviewStatus);
      toast(reviewState.current.ground_truth_saved ? '复核与样单标注已保存' : '复核结果已保存');
    } catch (error) {
      const conflict = error.code === 'review_revision_conflict';
      $('[data-save-state]').textContent = conflict ? '记录已更新，当前编辑已保留' : '保存失败，修改已保留';
      toast(conflict ? `${error.message}。当前编辑已保留，请重新打开回单后核对。` : error.message, 'danger');
    } finally {
      reviewState.reviewSaving = false; controls.forEach(([el, disabled]) => el.disabled = disabled);
      syncReviewReadiness();
    }
    if (saved) {
      $('[data-save-state]').textContent = '已保存'; $('[data-save-state]').classList.remove('unsaved');
      await continueReview().catch(error => toast(`已保存，下一张加载失败：${error.message}`, 'danger'));
      refreshVisibleResults().catch(() => toast('已保存，列表暂未刷新，可稍后刷新查看'));
    }
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

export function renderRecognitionVariants(item) {
  const grouped = [];
  const dzt = [];
  for (const [stage, variants] of Object.entries(item.recognition_variants || {})) {
    for (const variant of variants || []) {
      if (variant.method === 'danzhengtong') dzt.push({stage, variant});
      else grouped.push({stage, variant});
    }
  }
  if (dzt.length) {
    const trace = dzt.map(x => x.variant.details?.danzhengtong).find(Boolean);
    grouped.push({dzt, trace});
  }
  return grouped.map(entry => {
    if (entry.dzt) {
      const simulated = entry.dzt.every(x => x.variant.details?.danzhengtong?.simulated);
      const stages = entry.dzt.map(({stage, variant}) => ({stage: planLabels[stage] || stage, value: variant.value, error: variant.error, details: Object.fromEntries(Object.entries(variant.details || {}).filter(([k]) => k !== 'danzhengtong'))}));
      return `<details class="variant-details"><summary>单证通${simulated ? '（模拟）' : ''}</summary><pre>${escapeHtml(JSON.stringify({danzhengtong: entry.trace, stages}, null, 2))}</pre></details>`;
    }
    const {stage, variant} = entry;
    return `<details class="variant-details"><summary>${escapeHtml(planLabels[stage] || stage)} · ${escapeHtml(variant.method)}${variant.error ? ' · 识别失败' : ''}</summary><pre>${escapeHtml(JSON.stringify(variant.details || variant.error, null, 2))}</pre></details>`;
  }).join('');
}
