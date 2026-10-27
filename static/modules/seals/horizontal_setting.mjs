const labels = {unmarked:'未标注', present:'有', absent:'无'};
export const horizontalTextLabel = value => Object.hasOwn(labels, value) ? labels[value] : labels.unmarked;

export function horizontalSettingMarkup(standard, escapeHtml) {
  if (!['round', 'ellipse'].includes(standard?.shape)) return '';
  const current = Object.hasOwn(labels, standard.horizontal_text) ? standard.horizontal_text : 'unmarked';
  const group = `seal-horizontal-${escapeHtml(standard.id ?? 'current')}`;
  return `<section class="seal-horizontal-setting" aria-label="章内横排文字设置">
    <div class="seal-horizontal-row"><div class="seal-horizontal-field">
      <span class="seal-horizontal-label" id="${group}-label">章内横排文字</span>
      <div class="seal-horizontal-options" role="radiogroup" aria-labelledby="${group}-label">${['present', 'absent', 'unmarked'].map(value => `<label class="seal-horizontal-choice"><input type="radio" name="${group}" data-horizontal-text value="${value}"${value === current ? ' checked' : ''}><span>${labels[value]}</span></label>`).join('')}</div>
    </div><button type="button" class="seal-horizontal-save" data-save-horizontal-text disabled>保存</button></div>
    <div class="seal-horizontal-meta"><small>按章图实际内容设置，下次识别时生效。</small><span data-horizontal-feedback role="status" aria-live="polite"></span></div>
  </section>`;
}

export function bindHorizontalSetting(root, {standard, save, onSaved, setSaving = () => {}}) {
  const options = [...root.querySelectorAll('[data-horizontal-text]')];
  const button = root.querySelector('[data-save-horizontal-text]');
  const feedback = root.querySelector('[data-horizontal-feedback]');
  if (!options.length || !button || !feedback) return;
  let saving = false;
  const selected = () => options.find(input => input.checked)?.value;
  const sync = () => {
    options.forEach(input => { input.disabled = saving; });
    button.disabled = saving || !Object.hasOwn(labels, selected()) || selected() === (standard.horizontal_text || 'unmarked');
    button.textContent = saving ? '保存中…' : '保存';
  };
  options.forEach(input => input.addEventListener('change', () => {
    feedback.textContent = '';
    delete feedback.dataset.tone;
    sync();
  }));
  sync();
  button.addEventListener('click', async () => {
    if (saving) return;
    const value = selected();
    if (!Object.hasOwn(labels, value)) { feedback.textContent = '请选择有、无或未标注。'; return; }
    if (value === (standard.horizontal_text || 'unmarked')) return;
    saving = true; setSaving(true); sync();
    feedback.textContent = '';
    delete feedback.dataset.tone;
    try {
      const response = await save(standard.id, {horizontal_text:value, standard_revision:standard.revision});
      Object.assign(standard, response.standard);
      feedback.textContent = '已保存';
      setSaving(false);
      onSaved?.(response.standard);
    } catch (error) {
      feedback.textContent = error.message || '保存失败，请重试。';
      feedback.dataset.tone = 'error';
    } finally {
      saving = false; setSaving(false); sync();
    }
  });
}
