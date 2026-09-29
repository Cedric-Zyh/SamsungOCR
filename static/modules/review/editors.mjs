/**
 * Field confirmation editors for the review form.
 *
 * These controls maintain their own small editing models; the review
 * coordinator only supplies the shared domain rules and dirty-state callback.
 */
export function createReviewEditors({ReceiptWorkbench, $, $$, statusPill, escapeHtml, setReviewDirty}) {
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

  return {setupDateConfirmation, setupSealConfirmation, setupSignatureConfirmation};
}
